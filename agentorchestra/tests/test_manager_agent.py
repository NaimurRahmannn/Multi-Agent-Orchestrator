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
    assert agent.llm.max_tokens == 500
    assert agent.llm.timeout == 120
    assert agent.llm.additional_params["max_retries"] == 2
    assert agent.max_iter == 1
    assert agent.max_retry_limit == 0
    assert manager.MANAGER_SYSTEM_PROMPT in agent.backstory


def test_build_manager_task_requests_json_for_local_validation():
    agent = manager.build_manager_agent(groq_config())
    task = manager.build_manager_task(agent)

    assert task.agent is agent
    assert task.output_pydantic is None
    assert "Return only one JSON object" in task.expected_output
    assert task.tools == []
    assert task.async_execution is False
    assert task.max_retries == 0
    assert task.guardrail_max_retries == 0


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


def test_manager_router_selects_only_manager_credentials_from_settings():
    captured = []
    settings = Settings(
        groq_manager_api_key="manager-secret",
        groq_html_api_key="html-secret",
        groq_css_api_key="css-secret",
        groq_manager_model="manager-model",
        groq_html_model="html-model",
        groq_css_model="css-model",
    )
    router = manager.ManagerRouter(
        settings=settings,
        crew_factory=lambda groq: captured.append(groq) or object(),
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

    router.route({"target_page": "index.html", "instruction": "Change button color."})

    assert [configuration.api_key for configuration in captured] == ["manager-secret"]
    assert [configuration.model for configuration in captured] == ["manager-model"]


def test_missing_groq_configuration_raises_focused_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_MANAGER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MANAGER_MODEL", raising=False)
    router = manager.ManagerRouter(settings=Settings())

    with pytest.raises(ConfigurationError) as error:
        router.route({"target_page": "index.html", "instruction": "Change button color."})

    assert "GROQ_MANAGER_API_KEY" in str(error.value)
    assert "GROQ_MANAGER_MODEL" in str(error.value)
    assert "unit-test-secret" not in str(error.value)
