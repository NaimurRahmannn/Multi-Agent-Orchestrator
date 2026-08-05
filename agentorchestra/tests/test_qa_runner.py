import pytest

from agentorchestra.config import GroqConfiguration
from agentorchestra.exceptions import QAExecutionError, QAOutputError
from agentorchestra.services.qa_evidence import build_qa_evidence_bundle
from agentorchestra.services.qa_runner import QARunner
from tests.test_qa_evidence import report


def evidence():
    specialist_report = report()
    return build_qa_evidence_bundle(
        request=specialist_report.request,
        plan=specialist_report.plan,
        specialist_report=specialist_report,
        diff_report=specialist_report.diff_report,
    )


def accepted_output():
    return {
        "verdict": "accept",
        "criteria_results": [
            {
                "criterion": "The requested staged edit is present.",
                "status": "passed",
                "evidence": "The diff shows the requested edit.",
            }
        ],
        "reason": "All criteria passed.",
    }


def test_qa_runner_executes_once_and_records_latency():
    calls = []

    def agent_factory(**kwargs):
        calls.append(("agent", kwargs["groq"].model))
        return object()

    def task_factory(**kwargs):
        calls.append(("task", kwargs["evidence"].evidence_digest))
        return object()

    def crew_factory(agent, task):
        calls.append(("crew", agent is not None and task is not None))
        return object()

    def crew_executor(crew, inputs):
        calls.append(("execute", inputs["evidence_digest"]))
        return accepted_output()

    runner = QARunner(
        groq=GroqConfiguration(api_key="qa-secret", model="qa-model"),
        agent_factory=agent_factory,
        task_factory=task_factory,
        crew_factory=crew_factory,
        crew_executor=crew_executor,
        clock=iter([1.0, 1.25]).__next__,
    )

    result = runner.run(evidence())

    assert result.result.verdict == "accept"
    assert result.latency_ms == 250.0
    assert result.model == "groq/qa-model"
    assert [call[0] for call in calls] == ["agent", "task", "crew", "execute"]


def test_qa_runner_raises_output_error_for_bad_coverage():
    runner = QARunner(
        groq=GroqConfiguration(api_key="qa-secret", model="qa-model"),
        agent_factory=lambda **_kwargs: object(),
        task_factory=lambda **_kwargs: object(),
        crew_factory=lambda _agent, _task: object(),
        crew_executor=lambda _crew, _inputs: {
            "verdict": "accept",
            "criteria_results": [
                {"criterion": "Wrong", "status": "passed", "evidence": "No."}
            ],
            "reason": "Bad.",
        },
    )

    with pytest.raises(QAOutputError):
        runner.run(evidence())


def test_qa_runner_redacts_qa_secret_on_execution_failure():
    runner = QARunner(
        groq=GroqConfiguration(api_key="qa-secret", model="qa-model"),
        agent_factory=lambda **_kwargs: object(),
        task_factory=lambda **_kwargs: object(),
        crew_factory=lambda _agent, _task: object(),
        crew_executor=lambda _crew, _inputs: (_ for _ in ()).throw(
            RuntimeError("qa-secret exploded")
        ),
    )

    with pytest.raises(QAExecutionError) as error:
        runner.run(evidence())

    assert "qa-secret" not in str(error.value)
    assert "[redacted]" in str(error.value)
