from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from agentorchestra.agents.manager import ManagerRoutingInterface
from agentorchestra.config import Settings, ensure_runtime_directories, get_settings
from agentorchestra.models import (
    EditRequest,
    RoutingBenchmarkReport,
    RoutingEvidenceCase,
    RoutingEvidenceResult,
    TokenUsage,
    evaluate_routing_match,
)

DEFAULT_ROUTING_BENCHMARK_REPORT = Path("reports/routing/manager_routing_benchmark.json")
NowProvider = Callable[[], datetime]
Sleeper = Callable[[float], None]


class RoutingBenchmarkRunner:
    def __init__(
        self,
        router: ManagerRoutingInterface,
        *,
        model: str = "unknown",
        target_page_resolver: Callable[[RoutingEvidenceCase], str] | None = None,
        now_provider: NowProvider | None = None,
        delay_seconds: float = 0.0,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self._router = router
        self._model = model
        self._target_page_resolver = target_page_resolver or (lambda case: "index.html")
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._delay_seconds = max(0.0, delay_seconds)
        self._sleeper = sleeper

    def run(self, cases: Sequence[RoutingEvidenceCase]) -> RoutingBenchmarkReport:
        results: list[RoutingEvidenceResult] = []
        model = self._model
        for index, case in enumerate(cases):
            if index and self._delay_seconds:
                self._sleeper(self._delay_seconds)
            try:
                request = EditRequest(
                    target_page=self._target_page_resolver(case),
                    instruction=case.request,
                )
                manager_result = self._router.route(request)
                plan = manager_result.plan
                model = manager_result.model or model
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
                    validation_error=None,
                )
            except (ValidationError, ValueError) as exc:
                result = _invalid_result(case, f"Validation failed: {exc}")
            except Exception as exc:
                result = _invalid_result(case, f"Manager execution failed: {exc}")
            results.append(result)

        valid_count = sum(result.structurally_valid for result in results)
        correct_count = sum(result.routing_correct for result in results)
        total = len(results)
        return RoutingBenchmarkReport(
            generated_at=self._now_provider().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            model=model,
            total_cases=total,
            structurally_valid_cases=valid_count,
            correct_cases=correct_count,
            structural_validity_rate=_rate(valid_count, total),
            routing_accuracy=_rate(correct_count, total),
            results=results,
        )


def write_routing_benchmark_report(report: RoutingBenchmarkReport, path: Path) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def default_report_path(settings: Settings | None = None) -> Path:
    resolved_settings = settings or get_settings()
    ensure_runtime_directories(resolved_settings)
    return resolved_settings.project_root / DEFAULT_ROUTING_BENCHMARK_REPORT


def _invalid_result(case: RoutingEvidenceCase, error: str) -> RoutingEvidenceResult:
    return RoutingEvidenceResult(
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
        validation_error=error[:1_000],
    )


def _rate(count: int, total: int) -> str:
    if total == 0:
        return "0.0000"
    return f"{count / total:.4f}"
