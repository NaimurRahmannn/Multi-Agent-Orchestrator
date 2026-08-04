import pytest

from agentorchestra.agents import manager
from agentorchestra.config import GroqConfiguration, Settings
from agentorchestra.exceptions import ConfigurationError


def groq_config():
    return GroqConfiguration(api_key="unit-test-secret", model="llama-test")


def test_crewai_model_name_normalizes_groq_prefix():
    assert manager.crewai_model_name("llama-test") == "groq/llama-test"
    assert manager.crewai_model_name("groq/llama-test") == "groq/llama-test"


def test_crewai_model_name_rejects_blank_model():
    with pytest.raises(ValueError):
        manager.crewai_model_name("  ")


def test_build_manager_agent_uses_configured_model_and_no_tools():
    agent = manager.build_manager_agent(groq_config())

    assert agent.allow_delegation is False
    assert agent.verbose is False
    assert agent.tools == []
    assert agent.llm.model == "groq/llama-test"
    assert agent.max_iter == 1


def test_build_manager_task_requests_pydantic_output():
    agent = manager.build_manager_agent(groq_config())
    task = manager.build_manager_task(agent)

    assert task.agent is agent
    assert task.output_pydantic.__name__ == "ManagerRoutingPlan"
    assert task.tools == []
    assert task.async_execution is False


def test_llm_construction_is_deferred_until_route(monkeypatch):
    calls = []

    def fake_factory(groq):
        calls.append(groq.model)
        return object()

    router = manager.ManagerRouter(
        groq=groq_config(),
        crew_factory=fake_factory,
        crew_executor=lambda crew, inputs: {
            "status": "execute",
            "request_type": "css_change",
            "selected_specialists": ["css"],
            "routing_rationale": "CSS owns presentation changes.",
            "assignments": [{"agent": "css", "task": "Change button color."}],
            "acceptance_criteria": ["Button color changes."],
            "clarification_question": None,
            "rejection_reason": None,
        },
    )

    assert calls == []
    router.route({"target_page": "index.html", "instruction": "Change button color."})
    assert calls == ["llama-test"]


def test_missing_groq_configuration_raises_focused_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    router = manager.ManagerRouter(settings=Settings())

    with pytest.raises(ConfigurationError) as error:
        router.route({"target_page": "index.html", "instruction": "Change button color."})

    assert "GROQ_API_KEY" in str(error.value)
    assert "GROQ_MODEL" in str(error.value)
    assert "unit-test-secret" not in str(error.value)
