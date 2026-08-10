import json

from agentorchestra.agents.css_agent import build_css_agent, build_css_task
from agentorchestra.config import GroqConfiguration
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.tools import PatchEvidenceRecorder
from tests.specialist_helpers import execute_plan, request
from tests.test_workspace_service import make_settings


def test_css_agent_uses_page_read_scope_and_stylesheet_patch_scope(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-agent")
    recorder = PatchEvidenceRecorder()
    agent = build_css_agent(
        workspace=handle,
        target_page="index.html",
        recorder=recorder,
        groq=GroqConfiguration(api_key="secret", model="test-model"),
    )

    assert agent.allow_delegation is False
    assert agent.verbose is False
    assert not agent.memory
    assert agent.planning is False
    assert agent.reasoning is False
    assert agent.max_iter == 7
    assert agent.max_retry_limit == 0
    assert agent.llm.max_tokens == 500
    assert agent.llm.timeout == 120
    assert agent.llm.additional_params["max_retries"] == 2
    assert len(agent.tools) == 3
    assert agent.tools[0].allowed_files == ("index.html", "style.css")
    assert agent.tools[1].allowed_files == ("style.css",)
    assert agent.tools[1].name == "update_css_declaration"
    assert agent.tools[2].allowed_files == ("style.css",)
    assert "must call update_css_declaration" in agent.tools[2].description
    assert "must never be reported as completed" in agent.tools[2].description

    rejected = json.loads(
        agent.tools[2]._run(
            file="index.html", old_text="old", new_text="new", summary="Reject HTML."
        )
    )
    assert rejected["rejection_reason"] == "unauthorized_file"


def test_css_tools_are_not_shared_between_agents_or_recorders(tmp_path):
    settings = make_settings(tmp_path)
    first_handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-first")
    second_handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-second")
    first_recorder = PatchEvidenceRecorder()
    second_recorder = PatchEvidenceRecorder()
    groq = GroqConfiguration(api_key="secret", model="test-model")

    first = build_css_agent(
        workspace=first_handle,
        target_page="index.html",
        recorder=first_recorder,
        groq=groq,
    )
    second = build_css_agent(
        workspace=second_handle,
        target_page="index.html",
        recorder=second_recorder,
        groq=groq,
    )

    assert first.tools[0] is not second.tools[0]
    assert first.tools[1].recorder is first_recorder
    assert second.tools[1].recorder is second_recorder
    assert first.tools[2].recorder is first_recorder
    assert second.tools[2].recorder is second_recorder


def test_css_task_names_target_page_and_style_css_without_site_dump(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "css-task")
    agent = build_css_agent(
        workspace=handle,
        target_page="index.html",
        groq=GroqConfiguration(api_key="secret", model="test-model"),
    )
    plan = execute_plan("css")
    task = build_css_task(
        agent=agent,
        request=request(),
        assignment=plan.assignments[0],
        acceptance_criteria=plan.acceptance_criteria,
    )

    assert "index.html" in task.description
    assert "style.css" in task.description
    assert "structured CSS protocol" in task.description
    assert "prefer update_css_declaration" in task.description
    assert "completion only after status applied" in task.description
    assert "<!doctype html>" not in task.description
    assert task.output_pydantic.__name__ == "SpecialistCompletion"
