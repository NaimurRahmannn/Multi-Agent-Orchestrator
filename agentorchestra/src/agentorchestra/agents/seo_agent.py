from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentorchestra.agents.specialist_support import (
    build_specialist_agent,
    build_specialist_task,
    validate_target_page,
)
from agentorchestra.config import GroqAgentName, GroqConfiguration, Settings, get_settings
from agentorchestra.models import EditRequest, SpecialistAssignment, SpecialistName
from agentorchestra.prompts.seo import (
    SEO_AGENT_BACKSTORY,
    SEO_AGENT_GOAL,
    SEO_AGENT_ROLE,
    SEO_SHARED_RULES,
    SEO_TASK_EXPECTED_OUTPUT,
    build_seo_task_description,
)
from agentorchestra.seo_models import SEOCompletion, SEOExecutionMode
from agentorchestra.tools import PatchEvidenceRecorder, ProposePatchTool, ReadFileTool
from agentorchestra.workspace_models import WorkspaceHandle


def build_seo_agent(
    *,
    workspace: WorkspaceHandle,
    target_page: str,
    mode: SEOExecutionMode = SEOExecutionMode.EDIT,
    recorder: PatchEvidenceRecorder | None = None,
    settings: Settings | None = None,
    groq: GroqConfiguration | None = None,
    verbose: bool = False,
) -> Any:
    """Build one SEO Agent with edit or strictly read-only diagnostic tools."""
    validated_target = validate_target_page(target_page)
    validated_mode = SEOExecutionMode(mode)
    configuration = groq or (settings or get_settings()).require_groq_configuration(
        GroqAgentName.SEO
    )
    allowed = (validated_target,)
    tools: list[Any] = [ReadFileTool(handle=workspace, allowed_files=allowed)]
    if validated_mode is SEOExecutionMode.EDIT:
        tools.append(
            ProposePatchTool(
                handle=workspace,
                specialist=SpecialistName.SEO,
                allowed_files=allowed,
                recorder=recorder,
            )
        )
    return build_specialist_agent(
        groq=configuration,
        role=SEO_AGENT_ROLE,
        goal=SEO_AGENT_GOAL,
        backstory=f"{SEO_AGENT_BACKSTORY}\n\n{SEO_SHARED_RULES}",
        tools=tools,
        verbose=verbose,
    )


def build_seo_task(
    *,
    agent: Any,
    request: EditRequest,
    assignment: SpecialistAssignment,
    acceptance_criteria: Sequence[str],
    mode: SEOExecutionMode = SEOExecutionMode.EDIT,
) -> Any:
    description = build_seo_task_description(
        mode=SEOExecutionMode(mode),
        request=request,
        assignment=assignment,
        acceptance_criteria=acceptance_criteria,
    )
    return build_specialist_task(
        agent=agent,
        description=description,
        expected_output=SEO_TASK_EXPECTED_OUTPUT,
        output_model=SEOCompletion,
    )
