import pytest

from agentorchestra.agents.seo_agent import build_seo_agent, build_seo_task
from agentorchestra.config import GroqConfiguration, Settings
from agentorchestra.exceptions import ConfigurationError
from agentorchestra.seo_models import SEOExecutionMode
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.tools import PatchEvidenceRecorder
from tests.specialist_helpers import execute_plan, request
from tests.test_workspace_service import make_settings


def test_seo_edit_agent_has_only_bounded_page_read_and_patch_tools(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "seo-edit-agent")
    recorder = PatchEvidenceRecorder()

    agent = build_seo_agent(
        workspace=handle,
        target_page="index.html",
        mode=SEOExecutionMode.EDIT,
        recorder=recorder,
        groq=GroqConfiguration(api_key="secret", model="seo-model"),
    )

    assert [tool.name for tool in agent.tools] == ["read_file", "propose_patch"]
    assert agent.tools[0].allowed_files == ("index.html",)
    assert agent.tools[1].allowed_files == ("index.html",)
    assert agent.tools[1].recorder is recorder
    assert agent.allow_delegation is False
    assert not agent.memory
    assert agent.planning is False
    assert agent.max_retry_limit == 0


def test_seo_diagnostic_agent_is_strictly_read_only(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "seo-diagnostic-agent")

    agent = build_seo_agent(
        workspace=handle,
        target_page="index.html",
        mode=SEOExecutionMode.DIAGNOSTIC,
        groq=GroqConfiguration(api_key="secret", model="seo-model"),
    )
    plan = execute_plan("seo")
    task = build_seo_task(
        agent=agent,
        request=request(),
        assignment=plan.assignments[0],
        acceptance_criteria=plan.acceptance_criteria,
        mode=SEOExecutionMode.DIAGNOSTIC,
    )

    assert [tool.name for tool in agent.tools] == ["read_file"]
    assert task.output_pydantic.__name__ == "SEOCompletion"
    assert "Do not claim Lighthouse observations" in task.description
    assert "No patch tool is available" in task.description


def test_seo_agent_missing_configuration_is_focused(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "seo-no-config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_SEO_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_SEO_MODEL", raising=False)

    with pytest.raises(ConfigurationError) as error:
        build_seo_agent(
            workspace=handle,
            target_page="index.html",
            settings=Settings(project_root=settings.project_root),
        )

    assert "GROQ_SEO_API_KEY" in str(error.value)
    assert "GROQ_SEO_MODEL" in str(error.value)
