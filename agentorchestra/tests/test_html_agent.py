import pytest

from agentorchestra.agents.html_agent import build_html_agent, build_html_task
from agentorchestra.config import GroqConfiguration, Settings
from agentorchestra.exceptions import ConfigurationError
from agentorchestra.services.specialist_runner import SpecialistRunner
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.tools import PatchEvidenceRecorder
from tests.specialist_helpers import execute_plan, request
from tests.test_workspace_service import make_settings


def test_html_agent_uses_bound_tools_and_safe_configuration(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "html-agent")
    recorder = PatchEvidenceRecorder()
    agent = build_html_agent(
        workspace=handle,
        target_page="index.html",
        recorder=recorder,
        groq=GroqConfiguration(api_key="unit-test-secret", model="openai/gpt-oss-20b"),
    )

    assert agent.allow_delegation is False
    assert agent.verbose is False
    assert not agent.memory
    assert agent.planning is False
    assert agent.reasoning is False
    assert agent.max_iter == 7
    assert agent.max_retry_limit == 0
    assert agent.llm.model == "groq/openai/gpt-oss-20b"
    assert [tool.name for tool in agent.tools] == ["read_file", "propose_patch"]
    assert agent.tools[0].allowed_files == ("index.html",)
    assert agent.tools[1].allowed_files == ("index.html",)
    assert agent.tools[1].recorder is recorder


def test_html_task_requests_native_strict_completion(tmp_path):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "html-task")
    agent = build_html_agent(
        workspace=handle,
        target_page="index.html",
        groq=GroqConfiguration(api_key="secret", model="test-model"),
    )
    plan = execute_plan("html")
    task = build_html_task(
        agent=agent,
        request=request(),
        assignment=plan.assignments[0],
        acceptance_criteria=plan.acceptance_criteria,
    )

    assert task.agent is agent
    assert task.output_pydantic.__name__ == "SpecialistCompletion"
    assert task.guardrail_max_retries == 0
    assert task.async_execution is False
    assert "Allowed patch files" in task.description
    assert "old_text copied from its content field" in task.description


def test_specialist_runner_initialization_defers_llm_construction(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "agentorchestra.agents.specialist_support.build_specialist_llm",
        lambda groq: calls.append(groq) or object(),
    )

    SpecialistRunner(groq=GroqConfiguration(api_key="secret", model="test-model"))

    assert calls == []


def test_html_agent_missing_configuration_is_focused_and_redacted(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    handle = create_staged_copy(settings=settings, run_id_factory=lambda: "html-no-config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_HTML_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_HTML_MODEL", raising=False)

    with pytest.raises(ConfigurationError) as error:
        build_html_agent(
            workspace=handle,
            target_page="index.html",
            settings=Settings(project_root=settings.project_root),
        )

    assert "GROQ_HTML_API_KEY" in str(error.value)
    assert "GROQ_HTML_MODEL" in str(error.value)
    assert "unit-test-secret" not in str(error.value)
