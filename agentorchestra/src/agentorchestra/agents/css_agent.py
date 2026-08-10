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
from agentorchestra.prompts.specialists import (
    CSS_AGENT_BACKSTORY,
    CSS_AGENT_GOAL,
    CSS_AGENT_ROLE,
    CSS_OWNERSHIP_PROMPT,
    SHARED_SPECIALIST_RULES,
    SPECIALIST_TASK_EXPECTED_OUTPUT,
    build_specialist_task_description,
)
from agentorchestra.tools import (
    PatchEvidenceRecorder,
    ProposePatchTool,
    ReadFileTool,
    UpdateCSSDeclarationTool,
)
from agentorchestra.workspace_models import WorkspaceHandle


def build_css_agent(
    *,
    workspace: WorkspaceHandle,
    target_page: str,
    recorder: PatchEvidenceRecorder | None = None,
    settings: Settings | None = None,
    groq: GroqConfiguration | None = None,
    verbose: bool = False,
) -> Any:
    """Build one CSS Agent that may inspect its page but patch only shared style.css."""
    validated_target = validate_target_page(target_page)
    configuration = groq or (settings or get_settings()).require_groq_configuration(
        GroqAgentName.CSS
    )
    tools = [
        ReadFileTool(handle=workspace, allowed_files=tuple(sorted({validated_target, "style.css"}))),
        UpdateCSSDeclarationTool(
            handle=workspace,
            allowed_files=("style.css",),
            recorder=recorder,
        ),
        ProposePatchTool(
            handle=workspace,
            specialist=SpecialistName.CSS,
            allowed_files=("style.css",),
            recorder=recorder,
            description=(
                "CSS fallback for adding or removing declarations/rules only. Never use this "
                "tool to change the value of an existing CSS property; for any existing value "
                "change, you must call update_css_declaration instead. A rejected result is not "
                "success and must never be reported as completed."
            ),
        ),
    ]
    return build_specialist_agent(
        groq=configuration,
        role=CSS_AGENT_ROLE,
        goal=CSS_AGENT_GOAL,
        backstory=f"{CSS_AGENT_BACKSTORY}\n\n{CSS_OWNERSHIP_PROMPT}\n\n{SHARED_SPECIALIST_RULES}",
        tools=tools,
        verbose=verbose,
    )


def build_css_task(
    *,
    agent: Any,
    request: EditRequest,
    assignment: SpecialistAssignment,
    acceptance_criteria: Sequence[str],
) -> Any:
    description = build_specialist_task_description(
        specialist=SpecialistName.CSS,
        request=request,
        assignment=assignment,
        acceptance_criteria=acceptance_criteria,
        allowed_read_files=tuple(sorted({request.target_page, "style.css"})),
        allowed_patch_files=("style.css",),
    )
    return build_specialist_task(
        agent=agent,
        description=description,
        expected_output=SPECIALIST_TASK_EXPECTED_OUTPUT,
    )
