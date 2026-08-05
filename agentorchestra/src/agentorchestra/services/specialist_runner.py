from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agentorchestra.agents.css_agent import build_css_agent, build_css_task
from agentorchestra.agents.html_agent import build_html_agent, build_html_task
from agentorchestra.agents.manager import (
    crewai_model_name,
    disable_crewai_prompt_cache_breakpoints,
    normalize_token_usage,
)
from agentorchestra.agents.seo_agent import build_seo_agent, build_seo_task
from agentorchestra.agents.specialist_support import build_specialist_crew
from agentorchestra.config import GroqAgentName, GroqConfiguration, Settings, get_settings
from agentorchestra.exceptions import UnsupportedSpecialistError
from agentorchestra.models import EditRequest, SpecialistAssignment, SpecialistName, TokenUsage
from agentorchestra.seo_models import SEOCompletion, SEOExecutionMode
from agentorchestra.services.specialist_output import (
    extract_seo_completion,
    extract_specialist_completion,
)
from agentorchestra.services.workspace import read_file, validate_staged_site
from agentorchestra.specialist_models import (
    SpecialistCompletion,
    SpecialistRunResult,
    SpecialistRunStatus,
)
from agentorchestra.tools import PatchEvidenceRecorder
from agentorchestra.workspace_models import PatchExecutionResult, PatchStatus, WorkspaceHandle

AgentFactory = Callable[..., Any]
TaskFactory = Callable[..., Any]
CrewFactory = Callable[[Any, Any], Any]
CrewExecutor = Callable[[Any, dict[str, Any]], Any]
Clock = Callable[[], float]
RecorderFactory = Callable[[], PatchEvidenceRecorder]


def default_specialist_crew_executor(crew: Any, inputs: dict[str, Any]) -> Any:
    return crew.kickoff(inputs=inputs)


class SpecialistRunner:
    """Run one selected HTML, CSS, or SEO operation and derive status from evidence."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        groq: GroqConfiguration | None = None,
        agent_factories: Mapping[SpecialistName, AgentFactory] | None = None,
        task_factories: Mapping[SpecialistName, TaskFactory] | None = None,
        crew_factory: CrewFactory = build_specialist_crew,
        crew_executor: CrewExecutor = default_specialist_crew_executor,
        recorder_factory: RecorderFactory = PatchEvidenceRecorder,
        clock: Clock = time.perf_counter,
        verbose: bool = False,
    ) -> None:
        self._settings = settings
        self._groq = groq
        self._agent_factories = dict(
            agent_factories
            or {
                SpecialistName.HTML: build_html_agent,
                SpecialistName.CSS: build_css_agent,
                SpecialistName.SEO: build_seo_agent,
            }
        )
        self._task_factories = dict(
            task_factories
            or {
                SpecialistName.HTML: build_html_task,
                SpecialistName.CSS: build_css_task,
                SpecialistName.SEO: build_seo_task,
            }
        )
        self._crew_factory = crew_factory
        self._crew_executor = crew_executor
        self._recorder_factory = recorder_factory
        self._clock = clock
        self._verbose = verbose

    def run_specialist(
        self,
        request: EditRequest,
        assignment: SpecialistAssignment,
        acceptance_criteria: Sequence[str],
        workspace: WorkspaceHandle,
        mode: SEOExecutionMode = SEOExecutionMode.EDIT,
    ) -> SpecialistRunResult:
        validated_request = EditRequest.model_validate(request)
        validated_assignment = SpecialistAssignment.model_validate(assignment)
        specialist = validated_assignment.agent
        validated_mode = SEOExecutionMode(mode)
        if specialist not in {SpecialistName.HTML, SpecialistName.CSS, SpecialistName.SEO}:
            raise UnsupportedSpecialistError(
                f"Specialist {specialist.value} execution is not implemented."
            )
        if validated_mode is SEOExecutionMode.DIAGNOSTIC and specialist is not SpecialistName.SEO:
            raise UnsupportedSpecialistError(
                "Diagnostic mode is supported only by the SEO specialist."
            )

        started = self._clock()
        recorder: PatchEvidenceRecorder | None = None
        completion: SpecialistCompletion | SEOCompletion | None = None
        output: Any = None
        crew: Any = None
        groq: GroqConfiguration | None = None
        error: str | None = None

        try:
            validate_staged_site(workspace)
            read_file(
                workspace,
                file=validated_request.target_page,
                start_line=1,
                end_line=1,
                allowed_files=(validated_request.target_page,),
            )
            groq = self._resolve_groq(specialist)
            recorder = self._recorder_factory()
            agent_kwargs = {
                "workspace": workspace,
                "target_page": validated_request.target_page,
                "recorder": recorder,
                "groq": groq,
                "verbose": self._verbose,
            }
            if specialist is SpecialistName.SEO:
                agent_kwargs["mode"] = validated_mode
            agent = self._agent_factories[specialist](
                **agent_kwargs,
            )
            task_kwargs = {
                "agent": agent,
                "request": validated_request,
                "assignment": validated_assignment,
                "acceptance_criteria": acceptance_criteria,
            }
            if specialist is SpecialistName.SEO:
                task_kwargs["mode"] = validated_mode
            task = self._task_factories[specialist](
                **task_kwargs,
            )
            crew = self._crew_factory(agent, task)
            disable_crewai_prompt_cache_breakpoints()
            output = self._crew_executor(
                crew,
                {
                    "target_page": validated_request.target_page,
                    "instruction": validated_request.instruction,
                    "assignment": validated_assignment.task,
                },
            )
            completion = (
                extract_seo_completion(output)
                if specialist is SpecialistName.SEO
                else extract_specialist_completion(output)
            )
        except Exception as exc:
            secrets = self._all_secrets(groq)
            error = _safe_error("Specialist execution failed", exc, secrets=secrets)

        latency_ms = max(0.0, (self._clock() - started) * 1000)
        patch_results = list(recorder.snapshot()) if recorder is not None else []
        changed_files = sorted(
            {result.file for result in patch_results if result.status is PatchStatus.APPLIED}
        )

        usage = TokenUsage()
        if error is None:
            try:
                usage = normalize_token_usage(output, crew)
            except Exception as exc:
                secrets = self._all_secrets(groq)
                error = _safe_error("Specialist token evidence was invalid", exc, secrets=secrets)

        status, status_error = _derive_run_status(
            completion,
            patch_results,
            error,
            mode=validated_mode,
        )
        error = status_error
        model = _result_model(groq, self._groq, self._settings, specialist)
        return SpecialistRunResult(
            specialist=specialist,
            mode=validated_mode,
            assignment=validated_assignment.task,
            status=status,
            completion=completion,
            patch_results=patch_results,
            changed_files=changed_files,
            applied_patch_count=sum(
                result.status is PatchStatus.APPLIED for result in patch_results
            ),
            rejected_patch_count=sum(
                result.status is PatchStatus.REJECTED for result in patch_results
            ),
            latency_ms=float(latency_ms),
            token_usage=usage,
            model=model,
            error=error,
        )

    def _resolve_groq(self, specialist: SpecialistName) -> GroqConfiguration:
        if self._groq is not None:
            return self._groq
        return (self._settings or get_settings()).require_groq_configuration(
            GroqAgentName(specialist.value)
        )

    def _all_secrets(self, groq: GroqConfiguration | None) -> tuple[str, ...]:
        configured = (self._settings or get_settings()).groq_api_key_values
        if groq is None or groq.api_key in configured:
            return configured
        return (*configured, groq.api_key)


def _derive_run_status(
    completion: SpecialistCompletion | SEOCompletion | None,
    patch_results: Sequence[PatchExecutionResult],
    error: str | None,
    *,
    mode: SEOExecutionMode = SEOExecutionMode.EDIT,
) -> tuple[SpecialistRunStatus, str | None]:
    if error is not None:
        return SpecialistRunStatus.FAILED, error
    applied = any(result.status is PatchStatus.APPLIED for result in patch_results)
    if completion is None:
        return (
            SpecialistRunStatus.FAILED,
            "Specialist execution failed: completion output is missing.",
        )
    if mode is SEOExecutionMode.DIAGNOSTIC:
        if patch_results:
            return (
                SpecialistRunStatus.FAILED,
                "Specialist execution failed: diagnostic mode produced patch evidence.",
            )
        if isinstance(completion, SEOCompletion) and completion.status.value == "completed":
            return SpecialistRunStatus.SUCCEEDED, None
        if completion.status.value == "blocked":
            return SpecialistRunStatus.BLOCKED, None
        return (
            SpecialistRunStatus.FAILED,
            "Specialist execution failed: invalid diagnostic completion.",
        )
    if applied and completion.status.value == "completed":
        return SpecialistRunStatus.SUCCEEDED, None
    if not applied and completion.status.value == "blocked":
        return SpecialistRunStatus.BLOCKED, None
    if applied:
        return (
            SpecialistRunStatus.FAILED,
            "Specialist execution failed: blocked completion contradicts applied patch evidence.",
        )
    return (
        SpecialistRunStatus.FAILED,
        "Specialist execution failed: completed claim has no applied patch evidence.",
    )


def _result_model(
    resolved: GroqConfiguration | None,
    explicit: GroqConfiguration | None,
    settings: Settings | None,
    specialist: SpecialistName,
) -> str:
    if resolved is not None:
        return crewai_model_name(resolved.model)
    if explicit is not None:
        return crewai_model_name(explicit.model)
    configured_model = (
        settings.groq_model_for(GroqAgentName(specialist.value)) if settings is not None else None
    )
    if configured_model:
        return crewai_model_name(configured_model)
    return "groq/unavailable"


def _safe_error(prefix: str, exc: Exception, *, secrets: tuple[str, ...]) -> str:
    message = str(exc).replace("\n", " ").strip()
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return f"{prefix}: {message[:700]}"
