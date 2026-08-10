from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agentorchestra.agents.specialist_support import (
    build_specialist_agent,
    build_specialist_task,
    validate_target_page,
)
from agentorchestra.config import GroqAgentName, GroqConfiguration, Settings, get_settings
from agentorchestra.models import EditRequest, SpecialistAssignment
from agentorchestra.prompts.specialists import CSS_AGENT_BACKSTORY, CSS_AGENT_GOAL, CSS_AGENT_ROLE
from agentorchestra.prompts.style_planner import (
    CSS_STYLE_PLAN_EXPECTED_OUTPUT,
    CSS_STYLE_PLANNER_RULES,
    build_css_style_plan_description,
)
from agentorchestra.style_models import StyleIntentPlan
from agentorchestra.tools import PatchEvidenceRecorder
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
    """Build a tool-free CSS semantic planner bound to one selected page."""
    validate_target_page(target_page)
    configuration = groq or (settings or get_settings()).require_groq_configuration(
        GroqAgentName.CSS
    )
    del workspace, recorder
    return build_specialist_agent(
        groq=configuration,
        role=CSS_AGENT_ROLE,
        goal=CSS_AGENT_GOAL,
        backstory=f"{CSS_AGENT_BACKSTORY}\n\n{CSS_STYLE_PLANNER_RULES}",
        tools=[],
        verbose=verbose,
        max_iter=1,
    )


def build_css_task(
    *,
    agent: Any,
    request: EditRequest,
    assignment: SpecialistAssignment,
    acceptance_criteria: Sequence[str],
) -> Any:
    description = build_css_style_plan_description(
        request=request,
        assignment=assignment,
        acceptance_criteria=acceptance_criteria,
    )
    return build_specialist_task(
        agent=agent,
        description=description,
        expected_output=CSS_STYLE_PLAN_EXPECTED_OUTPUT,
        output_model=StyleIntentPlan,
    )
