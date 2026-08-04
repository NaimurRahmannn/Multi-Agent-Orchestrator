from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from agentorchestra.config import GroqConfiguration, Settings, get_settings
from agentorchestra.exceptions import ConfigurationError, ManagerExecutionError, ManagerOutputError
from agentorchestra.models import EditRequest, ManagerRoutingPlan, ManagerRunResult, TokenUsage
from agentorchestra.prompts.manager import (
    MANAGER_AGENT_BACKSTORY,
    MANAGER_AGENT_GOAL,
    MANAGER_AGENT_ROLE,
    MANAGER_ROUTING_TASK_DESCRIPTION,
    MANAGER_ROUTING_TASK_EXPECTED_OUTPUT,
    MANAGER_SYSTEM_PROMPT,
)

CrewExecutor = Callable[[Any, dict[str, Any]], Any]
CrewFactory = Callable[[GroqConfiguration], Any]
Clock = Callable[[], float]


class ManagerRoutingInterface(Protocol):
    def route(self, request: EditRequest) -> ManagerRunResult:
        """Route one validated edit request."""


def localize_crewai_paths() -> None:
    """Keep CrewAI runtime caches in a writable temp directory."""
    base = Path(tempfile.gettempdir()) / "agentorchestra-crewai"
    os.environ.setdefault("XDG_DATA_HOME", str(base / "data"))
    os.environ.setdefault("XDG_CONFIG_HOME", str(base / "config"))
    os.environ.setdefault("XDG_CACHE_HOME", str(base / "cache"))
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("CREWAI_DISABLE_TRACKING", "true")


def crewai_model_name(model: str) -> str:
    normalized = model.strip()
    if not normalized:
        raise ValueError("GROQ_MODEL must not be empty.")
    if normalized.startswith("groq/"):
        return normalized
    return f"groq/{normalized}"


def disable_crewai_prompt_cache_breakpoints() -> None:
    """Groq rejects CrewAI's provider-agnostic cache_breakpoint message flag."""
    localize_crewai_paths()
    from crewai.llms import cache as crewai_cache

    crewai_cache.mark_cache_breakpoint = lambda message: message


def build_manager_llm(groq: GroqConfiguration) -> Any:
    """Build the live Groq-backed CrewAI LLM only when routing is executed."""
    localize_crewai_paths()
    from crewai import LLM

    return LLM(
        model=crewai_model_name(groq.model),
        provider="groq",
        api_key=groq.api_key,
        temperature=0,
        max_tokens=800,
    )


def build_manager_agent(groq: GroqConfiguration) -> Any:
    """Build the production Manager Agent with no tools or delegation."""
    localize_crewai_paths()
    from crewai import Agent

    llm = build_manager_llm(groq)
    return Agent(
        role=MANAGER_AGENT_ROLE,
        goal=MANAGER_AGENT_GOAL,
        backstory=f"{MANAGER_AGENT_BACKSTORY}\n\n{MANAGER_SYSTEM_PROMPT}",
        allow_delegation=False,
        tools=[],
        llm=llm,
        function_calling_llm=llm,
        verbose=False,
        memory=False,
        planning=False,
        reasoning=False,
        max_iter=1,
        max_retry_limit=0,
    )


def build_manager_task(agent: Any) -> Any:
    """Build the single structured-output routing task."""
    localize_crewai_paths()
    from crewai import Task

    return Task(
        description=MANAGER_ROUTING_TASK_DESCRIPTION,
        expected_output=MANAGER_ROUTING_TASK_EXPECTED_OUTPUT,
        agent=agent,
        tools=[],
        async_execution=False,
        human_input=False,
        markdown=False,
        max_retries=0,
        guardrail_max_retries=0,
    )


def build_manager_crew(agent: Any, task: Any) -> Any:
    """Build a one-agent, one-task CrewAI crew for Manager routing."""
    localize_crewai_paths()
    from crewai import Crew, Process

    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
        memory=False,
        cache=False,
        planning=False,
    )


def default_crew_executor(crew: Any, inputs: dict[str, Any]) -> Any:
    return crew.kickoff(inputs=inputs)


def default_crew_factory(groq: GroqConfiguration) -> Any:
    agent = build_manager_agent(groq)
    task = build_manager_task(agent)
    return build_manager_crew(agent, task)


def normalize_token_usage(*sources: Any) -> TokenUsage:
    """Normalize known CrewAI token usage shapes without inventing missing values."""
    for source in sources:
        usage = _find_usage(source)
        if usage is None:
            continue
        values = {
            "prompt_tokens": _usage_value(usage, "prompt_tokens"),
            "completion_tokens": _usage_value(usage, "completion_tokens"),
            "total_tokens": _usage_value(usage, "total_tokens"),
        }
        if any(value is not None for value in values.values()):
            return TokenUsage(**values)
    return TokenUsage()


def extract_manager_plan(output: Any) -> ManagerRoutingPlan:
    """Extract and validate ManagerRoutingPlan from CrewAI output."""
    if isinstance(output, ManagerRoutingPlan):
        return output
    if isinstance(output, Mapping):
        return ManagerRoutingPlan.model_validate(output)

    pydantic_output = getattr(output, "pydantic", None)
    if pydantic_output is not None:
        return extract_manager_plan(pydantic_output)

    json_dict = getattr(output, "json_dict", None)
    if json_dict is not None:
        return ManagerRoutingPlan.model_validate(json_dict)

    tasks_output = getattr(output, "tasks_output", None)
    if tasks_output:
        last_task_output = tasks_output[-1]
        try:
            return extract_manager_plan(last_task_output)
        except (ManagerOutputError, ValidationError, ValueError):
            pass

    raw = getattr(output, "raw", None)
    if isinstance(raw, str) and raw.strip():
        return ManagerRoutingPlan.model_validate_json(_extract_json_object(raw))

    if isinstance(output, BaseModel):
        return ManagerRoutingPlan.model_validate(output.model_dump(mode="json"))

    raise ManagerOutputError("Manager response did not contain structured routing output.")


class ManagerRouter:
    """Route EditRequest objects through the production CrewAI Manager Agent."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        groq: GroqConfiguration | None = None,
        crew_factory: CrewFactory | None = None,
        crew_executor: CrewExecutor | None = None,
        clock: Clock = time.perf_counter,
    ) -> None:
        self._settings = settings
        self._groq = groq
        self._crew_factory = crew_factory or default_crew_factory
        self._crew_executor = crew_executor or default_crew_executor
        self._clock = clock

    def route(self, request: EditRequest) -> ManagerRunResult:
        validated_request = EditRequest.model_validate(request)
        groq = self._resolve_groq()
        disable_crewai_prompt_cache_breakpoints()
        crew = self._crew_factory(groq)

        started = self._clock()
        try:
            output = self._crew_executor(
                crew,
                {
                    "target_page": validated_request.target_page,
                    "instruction": validated_request.instruction,
                },
            )
        except Exception as exc:
            raise ManagerExecutionError(
                _safe_exception_message("Manager routing failed", exc, secrets=(groq.api_key,))
            ) from exc
        elapsed_ms = max(0.0, (self._clock() - started) * 1000)

        try:
            plan = extract_manager_plan(output)
        except (ValidationError, ValueError, ManagerOutputError) as exc:
            raise ManagerOutputError(
                _safe_exception_message("Invalid Manager routing output", exc, secrets=(groq.api_key,))
            ) from exc

        return ManagerRunResult(
            request=validated_request,
            plan=plan,
            latency_ms=float(elapsed_ms),
            token_usage=normalize_token_usage(output, crew),
            model=crewai_model_name(groq.model),
        )

    def _resolve_groq(self) -> GroqConfiguration:
        if self._groq is not None:
            return self._groq
        settings = self._settings or get_settings()
        try:
            return settings.require_groq_configuration()
        except ConfigurationError:
            raise


def _find_usage(source: Any) -> Any:
    if source is None:
        return None
    direct = getattr(source, "token_usage", None)
    if direct is not None:
        return direct
    metrics = getattr(source, "usage_metrics", None)
    if metrics is not None:
        return metrics
    if isinstance(source, Mapping):
        return source.get("token_usage") or source.get("usage_metrics") or source
    return None


def _usage_value(usage: Any, field: str) -> int | None:
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump(mode="json")
    value = usage.get(field) if isinstance(usage, Mapping) else getattr(usage, field, None)
    return value if value is not None else None


def _extract_json_object(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    decoder = json.JSONDecoder()
    start = stripped.find("{")
    if start == -1:
        raise ManagerOutputError("Manager response did not contain a JSON object.")
    _, end = decoder.raw_decode(stripped[start:])
    return stripped[start : start + end]


def _safe_exception_message(prefix: str, exc: Exception, *, secrets: tuple[str, ...] = ()) -> str:
    message = str(exc).replace("\n", " ").strip()
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return f"{prefix}: {message[:500]}"
