from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentorchestra.agents.specialist_support import (
    build_specialist_agent,
    build_specialist_task,
    validate_target_page,
)
from agentorchestra.config import GroqConfiguration, Settings, get_settings
from agentorchestra.models import EditRequest, SpecialistAssignment, SpecialistName
from agentorchestra.prompts.specialists import (
    HTML_AGENT_BACKSTORY,
    HTML_AGENT_GOAL,
    HTML_AGENT_ROLE,
    HTML_OWNERSHIP_PROMPT,
    SHARED_SPECIALIST_RULES,
    SPECIALIST_TASK_EXPECTED_OUTPUT,
    build_specialist_task_description,
)
from agentorchestra.tools import PatchEvidenceRecorder, ProposePatchTool, ReadFileTool
from agentorchestra.workspace_models import WorkspaceHandle


def build_html_agent(
    *,
    workspace: WorkspaceHandle,
    target_page: str,
    recorder: PatchEvidenceRecorder | None = None,
    settings: Settings | None = None,
    groq: GroqConfiguration | None = None,
    verbose: bool = False,
) -> Any:
    """Build one HTML Agent whose tools are bound to one staged target page."""
    validated_target = validate_target_page(target_page)
    configuration = groq or (settings or get_settings()).require_groq_configuration()
    allowed = (validated_target,)
    tools = [
        ReadFileTool(handle=workspace, allowed_files=allowed),
        ProposePatchTool(
            handle=workspace,
            specialist=SpecialistName.HTML,
            allowed_files=allowed,
            recorder=recorder,
        ),
    ]
    return build_specialist_agent(
        groq=configuration,
        role=HTML_AGENT_ROLE,
        goal=HTML_AGENT_GOAL,
        backstory=f"{HTML_AGENT_BACKSTORY}\n\n{HTML_OWNERSHIP_PROMPT}\n\n{SHARED_SPECIALIST_RULES}",
        tools=tools,
        verbose=verbose,
    )


def build_html_task(
    *,
    agent: Any,
    request: EditRequest,
    assignment: SpecialistAssignment,
    acceptance_criteria: Sequence[str],
) -> Any:
    description = build_specialist_task_description(
        specialist=SpecialistName.HTML,
        request=request,
        assignment=assignment,
        acceptance_criteria=acceptance_criteria,
        allowed_read_files=(request.target_page,),
        allowed_patch_files=(request.target_page,),
    )
    return build_specialist_task(
        agent=agent,
        description=description,
        expected_output=SPECIALIST_TASK_EXPECTED_OUTPUT,
    )
