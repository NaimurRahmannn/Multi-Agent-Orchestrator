from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentorchestra.config import ConfigurationError, get_settings
from agentorchestra.models import (
    ManagerRoutingPlan,
    RoutingEvidenceCase,
    RoutingEvidenceResult,
    RoutingStatus,
    SpecialistName,
    TokenUsage,
    evaluate_routing_match,
)

CASES: tuple[RoutingEvidenceCase, ...] = (
    RoutingEvidenceCase(
        case_id="css_only",
        request="Make the buttons use a darker teal background and slightly larger text.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="html_only",
        request="Fix the contact form so the email label is clearly connected to the email field.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML],
    ),
    RoutingEvidenceCase(
        case_id="html_css",
        request="Add a small services note to the homepage and style it so it stands apart.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML, SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="backend_unsupported",
        request="Create a backend endpoint that stores contact form submissions in a database.",
        expected_status=RoutingStatus.OUT_OF_SCOPE,
        expected_specialists=[],
    ),
    RoutingEvidenceCase(
        case_id="ambiguous",
        request="Make the website pop more.",
        expected_status=RoutingStatus.CLARIFICATION_REQUIRED,
        expected_specialists=[],
    ),
    RoutingEvidenceCase(
        case_id="alt_text",
        request="Improve the alt text for the lighthouse image on the about page.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.HTML],
    ),
    RoutingEvidenceCase(
        case_id="seo_diagnosis",
        request="Diagnose the homepage for basic on-page SEO issues.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO],
    ),
    RoutingEvidenceCase(
        case_id="seo_css",
        request="Improve the homepage title for SEO and make the hero call-to-action more prominent.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO, SpecialistName.CSS],
    ),
    RoutingEvidenceCase(
        case_id="title_meta",
        request="Update the about page title and meta description for search results.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.SEO],
    ),
    RoutingEvidenceCase(
        case_id="clear_css",
        request="Increase the spacing between navigation links.",
        expected_status=RoutingStatus.EXECUTE,
        expected_specialists=[SpecialistName.CSS],
    ),
)


SYSTEM_GUIDANCE = """
You are the isolated Phase 1 Manager feasibility probe for AgentOrchestra.
Classify static HTML/CSS website edit requests. Return only structured output matching the schema.
Allowed selected_specialists values are html, css, and seo. QA is never selectable.
Use execute only for supported static HTML, CSS, or narrow SEO work.
Use clarification_required for ambiguous edit requests.
Use out_of_scope for backend, database, JavaScript, deployment, or arbitrary live-site requests.
Return raw JSON only. Do not wrap it in Markdown.
"""


OUTPUT_CONTRACT = """
Return exactly one compact JSON object:
{"status":"execute|clarification_required|out_of_scope","request_type":"short category","selected_specialists":["html|css|seo"],"routing_rationale":"short explanation","assignments":[{"agent":"html|css|seo","task":"short task"}],"acceptance_criteria":["short criterion"],"clarification_question":"string or null","rejection_reason":"string or null"}

Rules:
- execute: selected_specialists and assignments are non-empty, one assignment per selected specialist, acceptance_criteria non-empty, routing_rationale non-empty, clarification_question null, rejection_reason null.
- clarification_required: selected_specialists, assignments, and acceptance_criteria empty; routing_rationale non-empty; clarification_question non-empty; rejection_reason null.
- out_of_scope: selected_specialists, assignments, and acceptance_criteria empty; routing_rationale non-empty; rejection_reason non-empty; clarification_question null.
- assignments must always be an array, never an object keyed by specialist.
- Use request_type values like css_change, html_repair, seo_diagnosis, unsupported_backend, or ambiguous_request. Do not copy placeholder text.
- HTML owns labels, attributes, alt text, and markup. CSS owns visual style only. SEO owns title, meta description, heading diagnosis, and search-result metadata.
"""


def _localize_crewai_paths() -> None:
    base = Path(tempfile.gettempdir()) / "agentorchestra-crewai"
    os.environ.setdefault("XDG_DATA_HOME", str(base / "data"))
    os.environ.setdefault("XDG_CONFIG_HOME", str(base / "config"))
    os.environ.setdefault("XDG_CACHE_HOME", str(base / "cache"))
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("CREWAI_DISABLE_TRACKING", "true")


def _crewai_model_name(model: str) -> str:
    normalized = model.strip()
    if not normalized:
        raise ValueError("GROQ_MODEL must not be empty.")
    if normalized.startswith("groq/"):
        return normalized
    return f"groq/{normalized}"


def _configure_groq_environment(settings: Any) -> None:
    os.environ["GROQ_API_KEY"] = settings.groq_api_key_value
    os.environ["GROQ_MODEL_NAME"] = _crewai_model_name(settings.groq_model_value)


def _disable_crewai_prompt_cache_breakpoints() -> None:
    """Groq rejects CrewAI's provider-agnostic cache_breakpoint message flag."""
    _localize_crewai_paths()
    from crewai.llms import cache as crewai_cache

    crewai_cache.mark_cache_breakpoint = lambda message: message


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


def _extract_plan(raw: str) -> ManagerRoutingPlan:
    if not raw:
        raise ValueError("CrewAI response did not include raw JSON.")
    return ManagerRoutingPlan.model_validate_json(_extract_json_object(raw))


def _build_messages(case: RoutingEvidenceCase, feedback: str | None = None) -> list[dict[str, str]]:
    feedback_text = f"\n\nPrevious attempt failed: {feedback}" if feedback else ""
    return [
        {"role": "system", "content": SYSTEM_GUIDANCE.strip()},
        {
            "role": "user",
            "content": f"{OUTPUT_CONTRACT.strip()}\n\nRequest: {case.request}{feedback_text}",
        },
    ]


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "rate_limit" in message or "rate limit" in message


def _short_error(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:280]


def _generate_plan_with_retries(
    llm: Any,
    case: RoutingEvidenceCase,
    manager: Any,
) -> tuple[str, ManagerRoutingPlan, int]:
    attempts = int(os.getenv("MANAGER_LLM_MAX_ATTEMPTS", "3"))
    base_delay = float(os.getenv("MANAGER_LLM_RETRY_DELAY_SECONDS", "5.0"))
    last_error: Exception | None = None
    feedback: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = llm.call(messages=_build_messages(case, feedback), from_agent=manager)
            if not isinstance(response, str) or not response.strip():
                raise ValueError("CrewAI LLM returned no text.")
            return response, _extract_plan(response), attempt
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            feedback = _short_error(exc)
            if _is_rate_limit_error(exc):
                time.sleep(base_delay * attempt)
            else:
                time.sleep(1.0)
    assert last_error is not None
    raise last_error


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
    _localize_crewai_paths()
    _configure_groq_environment(settings)
    from crewai import LLM, Agent

    _disable_crewai_prompt_cache_breakpoints()

    llm = LLM(
        model=os.environ["GROQ_MODEL_NAME"],
        provider="groq",
        api_key=settings.groq_api_key_value,
        temperature=0,
        max_tokens=650,
    )

    manager = Agent(
        role="Phase 1 Routing Manager",
        goal="Return a valid structured routing plan for a static website editing request.",
        backstory=SYSTEM_GUIDANCE,
        allow_delegation=False,
        tools=[],
        llm=llm,
        function_calling_llm=llm,
        verbose=False,
        max_iter=2,
    )

    started = time.perf_counter()
    raw_response, plan, attempts = _generate_plan_with_retries(llm, case, manager)
    latency_ms = round((time.perf_counter() - started) * 1000)
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
        latency_ms=latency_ms,
        token_usage=_token_usage_from_raw(_extract_usage(llm)),
    )
    trial = _trial_from_result(result, attempts=attempts)
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
        except (ValidationError, ValueError) as exc:
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
        except Exception as exc:
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

    output_path = PROJECT_ROOT / "reports" / "routing" / "phase1_manager_trials.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"trials": trials}, indent=2, sort_keys=True), encoding="utf-8")

    valid_count = sum(1 for trial in trials if trial["structural_validity"])
    correct_count = sum(1 for trial in trials if trial["routing_correctness"])
    print(f"Summary: {valid_count}/{len(trials)} structurally valid, {correct_count}/{len(trials)} routes matched")
    print(f"Report: {output_path}")
    return 1 if valid_count != len(trials) else 0


if __name__ == "__main__":
    raise SystemExit(main())
