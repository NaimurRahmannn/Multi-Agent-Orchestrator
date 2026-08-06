from __future__ import annotations

from typing import Any

from agentorchestra.agents.manager import crewai_model_name, localize_crewai_paths
from agentorchestra.config import GroqAgentName, GroqConfiguration, Settings, get_settings
from agentorchestra.pipeline_models import QAEvidenceBundle
from agentorchestra.prompts.qa import (
    QA_AGENT_BACKSTORY,
    QA_AGENT_GOAL,
    QA_AGENT_ROLE,
    QA_SYSTEM_PROMPT,
    QA_TASK_EXPECTED_OUTPUT,
    build_qa_task_description,
)


def build_qa_llm(groq: GroqConfiguration) -> Any:
    """Construct the configured Groq LLM only for a live QA invocation."""
    localize_crewai_paths()
    from crewai import LLM

    return LLM(
        model=crewai_model_name(groq.model),
        provider="groq",
        api_key=groq.api_key,
        temperature=0,
        max_tokens=700,
        max_retries=2,
        timeout=120,
    )


def build_qa_agent(
    *,
    settings: Settings | None = None,
    groq: GroqConfiguration | None = None,
    verbose: bool = False,
) -> Any:
    """Build one tool-free QA Agent using only the QA Groq configuration."""
    configuration = groq or (settings or get_settings()).require_groq_configuration(
        GroqAgentName.QA
    )
    localize_crewai_paths()
    from crewai import Agent

    llm = build_qa_llm(configuration)
    return Agent(
        role=QA_AGENT_ROLE,
        goal=QA_AGENT_GOAL,
        backstory=f"{QA_AGENT_BACKSTORY}\n\n{QA_SYSTEM_PROMPT}",
        allow_delegation=False,
        tools=[],
        llm=llm,
        verbose=verbose,
        memory=False,
        planning=False,
        reasoning=False,
        max_iter=1,
        max_retry_limit=0,
    )


def build_qa_task(*, agent: Any, evidence: QAEvidenceBundle) -> Any:
    """Build the single structured-output QA review task."""
    localize_crewai_paths()
    from crewai import Task

    return Task(
        description=build_qa_task_description(evidence),
        expected_output=QA_TASK_EXPECTED_OUTPUT,
        agent=agent,
        tools=[],
        async_execution=False,
        human_input=False,
        markdown=False,
        max_retries=0,
        guardrail_max_retries=0,
    )


def build_qa_crew(agent: Any, task: Any) -> Any:
    """Build a one-agent, one-task CrewAI crew for QA review."""
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
