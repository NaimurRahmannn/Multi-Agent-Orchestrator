import pytest

from agentorchestra.agents.qa_agent import build_qa_agent, build_qa_task
from agentorchestra.config import GroqConfiguration, Settings
from agentorchestra.exceptions import ConfigurationError
from agentorchestra.prompts.qa import QA_SYSTEM_PROMPT
from tests.test_qa_evidence import report


def test_qa_agent_uses_qa_model_and_has_no_tools():
    agent = build_qa_agent(groq=GroqConfiguration(api_key="qa-secret", model="qa-model"))

    assert agent.allow_delegation is False
    assert agent.tools == []
    assert agent.verbose is False
    assert not agent.memory
    assert agent.planning is False
    assert agent.reasoning is False
    assert agent.max_iter == 1
    assert agent.max_retry_limit == 0
    assert agent.llm.model == "groq/qa-model"
    assert agent.llm.max_tokens == 700
    assert agent.llm.timeout == 120
    assert agent.llm.additional_params["max_retries"] == 2


def test_qa_task_requests_strict_qa_result():
    from agentorchestra.services.qa_evidence import build_qa_evidence_bundle

    specialist_report = report()
    evidence = build_qa_evidence_bundle(
        request=specialist_report.request,
        plan=specialist_report.plan,
        specialist_report=specialist_report,
        diff_report=specialist_report.diff_report,
    )
    agent = build_qa_agent(groq=GroqConfiguration(api_key="qa-secret", model="qa-model"))
    task = build_qa_task(agent=agent, evidence=evidence)

    assert task.agent is agent
    assert task.tools == []
    assert task.output_pydantic.__name__ == "QAResult"
    assert task.max_retries == 0
    assert task.guardrail_max_retries == 0
    assert "Preserve Manager criterion wording exactly" in task.description
    assert "qa-secret" not in task.description
    assert "normalized Lighthouse SEO evidence" in QA_SYSTEM_PROMPT
    assert "not proof of unrelated HTML/CSS criteria" in QA_SYSTEM_PROMPT
    assert "Do not claim search ranking improvement" in QA_SYSTEM_PROMPT


def test_qa_agent_missing_configuration_is_focused_and_redacted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_MANAGER_API_KEY", "manager-secret")
    monkeypatch.setenv("GROQ_MANAGER_MODEL", "manager-model")
    monkeypatch.delenv("GROQ_QA_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_QA_MODEL", raising=False)

    with pytest.raises(ConfigurationError) as error:
        build_qa_agent(settings=Settings(project_root=tmp_path))

    assert "GROQ_QA_API_KEY" in str(error.value)
    assert "GROQ_QA_MODEL" in str(error.value)
    assert "manager-secret" not in str(error.value)
