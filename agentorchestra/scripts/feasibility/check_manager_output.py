from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pydantic import ValidationError

from agentorchestra.agents.manager import (
    ManagerRouter,
    crewai_model_name,
    disable_crewai_prompt_cache_breakpoints,
)
from agentorchestra.config import ConfigurationError, get_settings
from agentorchestra.evaluation.routing_cases import DIAGNOSTIC_ROUTING_CASES, target_page_for_case
from agentorchestra.exceptions import ManagerExecutionError, ManagerOutputError
from agentorchestra.models import (
    EditRequest,
    RoutingEvidenceCase,
    RoutingEvidenceResult,
    TokenUsage,
    evaluate_routing_match,
)

CASES: tuple[RoutingEvidenceCase, ...] = DIAGNOSTIC_ROUTING_CASES
_crewai_model_name = crewai_model_name
_disable_crewai_prompt_cache_breakpoints = disable_crewai_prompt_cache_breakpoints


def _extract_usage(*sources: Any) -> dict[str, Any]:
    for source in sources:
        usage = getattr(source, "token_usage", None) or getattr(source, "usage_metrics", None)
        if usage is None:
            continue
        if hasattr(usage, "model_dump"):
            return usage.model_dump(mode="json")
        if isinstance(usage, dict):
            return usage
    return {}


def _token_usage_from_raw(raw_usage: dict[str, Any]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=raw_usage.get("prompt_tokens"),
        completion_tokens=raw_usage.get("completion_tokens"),
        total_tokens=raw_usage.get("total_tokens"),
    )


def _extract_json_object(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    decoder = json.JSONDecoder()
    start = stripped.find("{")
    if start == -1:
        raise ValueError("CrewAI response did not contain a JSON object.")
    _, end = decoder.raw_decode(stripped[start:])
    return stripped[start : start + end]


def _extract_plan(raw: str):
    if not raw:
        raise ValueError("CrewAI response did not include raw JSON.")
    from agentorchestra.models import ManagerRoutingPlan

    return ManagerRoutingPlan.model_validate_json(_extract_json_object(raw))


def _short_error(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:280]


def _trial_from_result(result: RoutingEvidenceResult, attempts: int | None = None) -> dict[str, Any]:
    token_usage = result.token_usage.model_dump(mode="json")
    if all(value is None for value in token_usage.values()):
        token_usage = {}
    trial = {
        "case": result.case_id,
        "request": result.request,
        "expected_route": {
            "status": result.expected_status.value,
            "specialists": [specialist.value for specialist in result.expected_specialists],
        },
        "actual_route": None
        if result.actual_status is None
        else {
            "status": result.actual_status.value,
            "specialists": [specialist.value for specialist in result.actual_specialists],
        },
        "routing_rationale": result.routing_rationale,
        "structural_validity": result.structurally_valid,
        "routing_correctness": result.routing_correct,
        "latency_seconds": None if result.latency_ms is None else result.latency_ms / 1000,
        "token_usage": token_usage,
    }
    if attempts is not None:
        trial["attempts"] = attempts
    if result.validation_error:
        trial["error"] = result.validation_error
    return trial


def _run_case(case: RoutingEvidenceCase, settings: Any) -> dict[str, Any]:
    router = ManagerRouter(settings=settings)
    manager_result = router.route(
        EditRequest(target_page=target_page_for_case(case), instruction=case.request)
    )
    plan = manager_result.plan
    result = RoutingEvidenceResult(
        case_id=case.case_id,
        request=case.request,
        expected_status=case.expected_status,
        expected_specialists=case.expected_specialists,
        actual_status=plan.status,
        actual_specialists=plan.selected_specialists,
        routing_rationale=plan.routing_rationale,
        structurally_valid=True,
        routing_correct=evaluate_routing_match(case, plan),
        latency_ms=round(manager_result.latency_ms),
        token_usage=manager_result.token_usage,
    )
    trial = _trial_from_result(result, attempts=1)
    trial["plan"] = plan.model_dump(mode="json")
    return trial


def main() -> int:
    settings = get_settings()
    try:
        settings.require_groq()
    except ConfigurationError as exc:
        print(f"FAIL Manager check configuration: {exc}")
        return 1

    case_limit = int(os.getenv("MANAGER_CASE_LIMIT", str(len(CASES))))
    selected_cases = CASES[:case_limit]

    trials: list[dict[str, Any]] = []
    for case in selected_cases:
        try:
            trial = _run_case(case, settings)
        except (ValidationError, ValueError, ManagerOutputError) as exc:
            result = RoutingEvidenceResult(
                case_id=case.case_id,
                request=case.request,
                expected_status=case.expected_status,
                expected_specialists=case.expected_specialists,
                actual_status=None,
                actual_specialists=[],
                routing_rationale=None,
                structurally_valid=False,
                routing_correct=False,
                latency_ms=None,
                token_usage=TokenUsage(),
                validation_error=str(exc),
            )
            trial = _trial_from_result(result)
        except ManagerExecutionError as exc:
            result = RoutingEvidenceResult(
                case_id=case.case_id,
                request=case.request,
                expected_status=case.expected_status,
                expected_specialists=case.expected_specialists,
                actual_status=None,
                actual_specialists=[],
                routing_rationale=None,
                structurally_valid=False,
                routing_correct=False,
                latency_ms=None,
                token_usage=TokenUsage(),
                validation_error=f"Live manager request failed: {exc}",
            )
            trial = _trial_from_result(result)
        trials.append(trial)
        marker = "valid" if trial["structural_validity"] else "invalid"
        route = "matched" if trial["routing_correctness"] else "mismatched"
        print(f"{case.case_id}: structure={marker}, route={route}")
        time.sleep(float(os.getenv("MANAGER_CASE_DELAY_SECONDS", "10.0")))

    output_path = PROJECT_ROOT / "reports" / "routing" / "manager_trials.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"trials": trials}, indent=2, sort_keys=True), encoding="utf-8")

    valid_count = sum(1 for trial in trials if trial["structural_validity"])
    correct_count = sum(1 for trial in trials if trial["routing_correctness"])
    print(f"Summary: {valid_count}/{len(trials)} structurally valid, {correct_count}/{len(trials)} routes matched")
    print(f"Report: {output_path}")
    return 1 if valid_count != len(trials) else 0


if __name__ == "__main__":
    raise SystemExit(main())
