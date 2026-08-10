from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from agentorchestra.agents.manager import crewai_model_name, localize_crewai_paths
from agentorchestra.config import GroqConfiguration
from agentorchestra.models import EditRequest
from agentorchestra.specialist_models import SpecialistCompletion


def build_specialist_llm(groq: GroqConfiguration) -> Any:
    """Construct the configured Groq LLM only for a live specialist invocation."""
    localize_crewai_paths()
    from crewai import LLM

    return LLM(
        model=crewai_model_name(groq.model),
        provider="groq",
        api_key=groq.api_key,
        temperature=0,
        max_tokens=500,
        max_retries=2,
        timeout=120,
    )


def build_specialist_agent(
    *,
    groq: GroqConfiguration,
    role: str,
    goal: str,
    backstory: str,
    tools: Sequence[Any],
    verbose: bool,
    max_iter: int = 7,
) -> Any:
    localize_crewai_paths()
    from crewai import Agent

    llm = build_specialist_llm(groq)
    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        allow_delegation=False,
        tools=list(tools),
        llm=llm,
        verbose=verbose,
        memory=False,
        planning=False,
        reasoning=False,
        max_iter=max_iter,
        max_retry_limit=0,
    )


def build_specialist_task(
    *,
    agent: Any,
    description: str,
    expected_output: str,
    output_model: type[BaseModel] = SpecialistCompletion,
) -> Any:
    localize_crewai_paths()
    from crewai import Task

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
        tools=list(agent.tools),
        output_pydantic=output_model,
        async_execution=False,
        human_input=False,
        markdown=False,
        max_retries=0,
        guardrail_max_retries=0,
    )


def build_specialist_crew(agent: Any, task: Any) -> Any:
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


def validate_target_page(target_page: str) -> str:
    """Apply the existing EditRequest filename contract to trusted agent setup."""
    return EditRequest(
        target_page=target_page, instruction="Validate specialist target page."
    ).target_page
