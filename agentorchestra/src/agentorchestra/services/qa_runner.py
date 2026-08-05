from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from agentorchestra.agents.manager import (
    crewai_model_name,
    disable_crewai_prompt_cache_breakpoints,
    normalize_token_usage,
)
from agentorchestra.agents.qa_agent import build_qa_agent, build_qa_crew, build_qa_task
from agentorchestra.config import GroqAgentName, GroqConfiguration, Settings, get_settings
from agentorchestra.exceptions import QAExecutionError, QAOutputError
from agentorchestra.models import TokenUsage
from agentorchestra.pipeline_models import QAEvidenceBundle, QARunResult
from agentorchestra.services.qa_output import extract_qa_result

AgentFactory = Callable[..., Any]
TaskFactory = Callable[..., Any]
CrewFactory = Callable[[Any, Any], Any]
CrewExecutor = Callable[[Any, dict[str, Any]], Any]
Clock = Callable[[], float]


def default_qa_crew_executor(crew: Any, inputs: dict[str, Any]) -> Any:
    return crew.kickoff(inputs=inputs)


class QARunner:
    """Run exactly one tool-free QA CrewAI review and return validated evidence."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        groq: GroqConfiguration | None = None,
        agent_factory: AgentFactory = build_qa_agent,
        task_factory: TaskFactory = build_qa_task,
        crew_factory: CrewFactory = build_qa_crew,
        crew_executor: CrewExecutor = default_qa_crew_executor,
        clock: Clock = time.perf_counter,
        verbose: bool = False,
    ) -> None:
        self._settings = settings
        self._groq = groq
        self._agent_factory = agent_factory
        self._task_factory = task_factory
        self._crew_factory = crew_factory
        self._crew_executor = crew_executor
        self._clock = clock
        self._verbose = verbose

    def run(self, evidence: QAEvidenceBundle) -> QARunResult:
        validated_evidence = QAEvidenceBundle.model_validate(evidence)
        groq = self._resolve_groq()
        started = self._clock()
        output: Any = None
        crew: Any = None
        try:
            agent = self._agent_factory(
                settings=self._settings,
                groq=groq,
                verbose=self._verbose,
            )
            task = self._task_factory(agent=agent, evidence=validated_evidence)
            crew = self._crew_factory(agent, task)
            disable_crewai_prompt_cache_breakpoints()
            output = self._crew_executor(
                crew,
                {"evidence_digest": validated_evidence.evidence_digest},
            )
            result = extract_qa_result(
                output,
                acceptance_criteria=validated_evidence.acceptance_criteria,
                secrets=(groq.api_key,),
            )
        except QAOutputError:
            raise
        except Exception as exc:
            raise QAExecutionError(
                _safe_error("QA execution failed", exc, secrets=(groq.api_key,))
            ) from exc

        latency_ms = max(0.0, (self._clock() - started) * 1000)
        try:
            usage = normalize_token_usage(output, crew)
        except Exception:
            usage = TokenUsage()
        return QARunResult(
            result=result,
            latency_ms=float(latency_ms),
            token_usage=usage,
            model=crewai_model_name(groq.model),
            evidence_digest=validated_evidence.evidence_digest,
            site_content_digest=validated_evidence.site_content_digest,
        )

    def _resolve_groq(self) -> GroqConfiguration:
        if self._groq is not None:
            return self._groq
        return (self._settings or get_settings()).require_groq_configuration(GroqAgentName.QA)


def _safe_error(prefix: str, exc: Exception, *, secrets: tuple[str, ...]) -> str:
    message = str(exc).replace("\n", " ").strip()
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return f"{prefix}: {message[:700]}"
