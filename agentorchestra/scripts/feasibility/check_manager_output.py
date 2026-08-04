from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentorchestra.config import ConfigurationError, get_settings
from agentorchestra.models import ManagerRoutingPlan, RoutingStatus, SpecialistName


@dataclass(frozen=True)
class RoutingCase:
    name: str
    request: str
    expected_status: RoutingStatus
    expected_specialists: tuple[SpecialistName, ...]


CASES: tuple[RoutingCase, ...] = (
    RoutingCase(
        "css_only",
        "Make the buttons use a darker teal background and slightly larger text.",
        RoutingStatus.EXECUTE,
        (SpecialistName.CSS,),
    ),
    RoutingCase(
        "html_only",
        "Fix the contact form so the email label is clearly connected to the email field.",
        RoutingStatus.EXECUTE,
        (SpecialistName.HTML,),
    ),
    RoutingCase(
        "html_css",
        "Add a small services note to the homepage and style it so it stands apart.",
        RoutingStatus.EXECUTE,
        (SpecialistName.HTML, SpecialistName.CSS),
    ),
    RoutingCase(
        "backend_unsupported",
        "Create a backend endpoint that stores contact form submissions in a database.",
        RoutingStatus.OUT_OF_SCOPE,
        (),
    ),
    RoutingCase(
        "ambiguous",
        "Make the website pop more.",
        RoutingStatus.CLARIFICATION_REQUIRED,
        (),
    ),
    RoutingCase(
        "alt_text",
        "Improve the alt text for the lighthouse image on the about page.",
        RoutingStatus.EXECUTE,
        (SpecialistName.HTML,),
    ),
    RoutingCase(
        "seo_diagnosis",
        "Diagnose the homepage for basic on-page SEO issues.",
        RoutingStatus.EXECUTE,
        (SpecialistName.SEO,),
    ),
    RoutingCase(
        "seo_css",
        "Improve the homepage title for SEO and make the hero call-to-action more prominent.",
        RoutingStatus.EXECUTE,
        (SpecialistName.SEO, SpecialistName.CSS),
    ),
    RoutingCase(
        "title_meta",
        "Update the about page title and meta description for search results.",
        RoutingStatus.EXECUTE,
        (SpecialistName.SEO,),
    ),
    RoutingCase(
        "clear_css",
        "Increase the spacing between navigation links.",
        RoutingStatus.EXECUTE,
        (SpecialistName.CSS,),
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
Return exactly one JSON object with these keys:
status: one of execute, clarification_required, out_of_scope
request_type: short request category
selected_specialists: array containing only html, css, seo
routing_rationale: non-empty string for execute, otherwise null
assignments: one object per selected specialist, each with agent and task
acceptance_criteria: non-empty array for execute, empty array otherwise
clarification_question: non-empty string only for clarification_required, otherwise null
rejection_reason: non-empty string only for out_of_scope, otherwise null
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


def _extract_usage(result: Any, crew: Any) -> dict[str, Any]:
    for source in (result, crew):
        usage = getattr(source, "token_usage", None) or getattr(source, "usage_metrics", None)
        if usage is None:
            continue
        if hasattr(usage, "model_dump"):
            return usage.model_dump(mode="json")
        if isinstance(usage, dict):
            return usage
    return {}


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


def _extract_plan(result: Any, task: Any) -> ManagerRoutingPlan:
    raw = getattr(result, "raw", None) or getattr(getattr(task, "output", None), "raw", None)
    if not raw:
        raise ValueError("CrewAI response did not include raw JSON.")
    return ManagerRoutingPlan.model_validate_json(_extract_json_object(raw))


def _run_case(case: RoutingCase, settings: Any) -> dict[str, Any]:
    _localize_crewai_paths()
    _configure_groq_environment(settings)
    from crewai import Agent, Crew, LLM, Process, Task

    llm = LLM(
        model=os.environ["GROQ_MODEL_NAME"],
        provider="groq",
        api_key=settings.groq_api_key_value,
        temperature=0,
        max_tokens=450,
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
    task = Task(
        description=(
            f"Request: {case.request}\n\n"
            f"{OUTPUT_CONTRACT}\n"
            "Keep rationale, assignments, and criteria concise."
        ),
        expected_output="A raw JSON object that validates as ManagerRoutingPlan.",
        agent=manager,
    )
    crew = Crew(
        agents=[manager],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=llm,
    )

    started = time.perf_counter()
    result = crew.kickoff()
    latency_seconds = round(time.perf_counter() - started, 3)
    plan = _extract_plan(result, task)
    actual_specialists = tuple(plan.selected_specialists)
    routing_correct = (
        plan.status == case.expected_status and set(actual_specialists) == set(case.expected_specialists)
    )
    return {
        "case": case.name,
        "request": case.request,
        "expected_route": {
            "status": case.expected_status.value,
            "specialists": [specialist.value for specialist in case.expected_specialists],
        },
        "actual_route": {
            "status": plan.status.value,
            "specialists": [specialist.value for specialist in actual_specialists],
        },
        "routing_rationale": plan.routing_rationale,
        "structural_validity": True,
        "routing_correctness": routing_correct,
        "latency_seconds": latency_seconds,
        "token_usage": _extract_usage(result, crew),
        "plan": plan.model_dump(mode="json"),
    }


def main() -> int:
    settings = get_settings()
    try:
        settings.require_groq()
    except ConfigurationError as exc:
        print(f"FAIL Manager check configuration: {exc}")
        return 1

    trials: list[dict[str, Any]] = []
    for case in CASES:
        try:
            trial = _run_case(case, settings)
        except (ValidationError, ValueError) as exc:
            trial = {
                "case": case.name,
                "request": case.request,
                "expected_route": {
                    "status": case.expected_status.value,
                    "specialists": [specialist.value for specialist in case.expected_specialists],
                },
                "actual_route": None,
                "routing_rationale": None,
                "structural_validity": False,
                "routing_correctness": False,
                "latency_seconds": None,
                "token_usage": {},
                "error": str(exc),
            }
        except Exception as exc:
            trial = {
                "case": case.name,
                "request": case.request,
                "expected_route": {
                    "status": case.expected_status.value,
                    "specialists": [specialist.value for specialist in case.expected_specialists],
                },
                "actual_route": None,
                "routing_rationale": None,
                "structural_validity": False,
                "routing_correctness": False,
                "latency_seconds": None,
                "token_usage": {},
                "error": f"Live manager request failed: {exc}",
            }
        trials.append(trial)
        marker = "valid" if trial["structural_validity"] else "invalid"
        route = "matched" if trial["routing_correctness"] else "mismatched"
        print(f"{case.name}: structure={marker}, route={route}")
        time.sleep(float(os.getenv("MANAGER_CASE_DELAY_SECONDS", "1.0")))

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
