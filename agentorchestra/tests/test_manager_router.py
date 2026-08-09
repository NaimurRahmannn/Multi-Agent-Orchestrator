import json

import pytest
from pydantic import BaseModel, ValidationError

from agentorchestra.agents.manager import ManagerRouter
from agentorchestra.config import GroqConfiguration
from agentorchestra.exceptions import ManagerExecutionError, ManagerOutputError
from agentorchestra.models import EditRequest, ManagerRoutingPlan


def execute_payload(**overrides):
    payload = {
        "status": "execute",
        "request_type": "css_edit",
        "selected_specialists": ["css"],
        "routing_rationale": "CSS owns presentation changes.",
        "assignments": [{"agent": "css", "task": "Change button color."}],
        "acceptance_criteria": ["Button color changes."],
        "clarification_question": None,
        "rejection_reason": None,
    }
    payload.update(overrides)
    return payload


def router_for(output, *, calls=None, clock_values=None, secret="unit-test-secret"):
    values = iter(clock_values or [1.0, 1.25])

    def fake_executor(crew, inputs):
        if calls is not None:
            calls.append(inputs)
        if isinstance(output, Exception):
            raise output
        return output

    return ManagerRouter(
        groq=GroqConfiguration(api_key=secret, model="llama-test"),
        crew_factory=lambda groq: object(),
        crew_executor=fake_executor,
        clock=lambda: next(values),
    )


def test_valid_execute_result():
    result = router_for(execute_payload()).route(
        EditRequest(target_page="index.html", instruction="Change button color.")
    )

    assert result.plan.status == "execute"
    assert result.plan.selected_specialists == ["css"]


def test_valid_multi_specialist_result():
    result = router_for(
        execute_payload(
            request_type="html_css_edit",
            selected_specialists=["html", "css"],
            assignments=[
                {"agent": "html", "task": "Add alt text."},
                {"agent": "css", "task": "Make heading bigger."},
            ],
            acceptance_criteria=["Alt text exists.", "Heading is visually larger."],
        )
    ).route({"target_page": "index.html", "instruction": "Make heading bigger and add alt text."})

    assert result.plan.selected_specialists == ["html", "css"]


def test_valid_clarification_result():
    output = {
        "status": "clarification_required",
        "request_type": "ambiguous_request",
        "selected_specialists": [],
        "routing_rationale": "The request does not name a concrete outcome.",
        "assignments": [],
        "acceptance_criteria": [],
        "clarification_question": "What should change?",
        "rejection_reason": None,
    }

    assert router_for(output).route({"target_page": "index.html", "instruction": "Make it better"}).plan.status == "clarification_required"


def test_valid_out_of_scope_result():
    output = {
        "status": "out_of_scope",
        "request_type": "unsupported_backend",
        "selected_specialists": [],
        "routing_rationale": "Backend validation is outside the static-site scope.",
        "assignments": [],
        "acceptance_criteria": [],
        "clarification_question": None,
        "rejection_reason": "Backend validation is not supported.",
    }

    assert router_for(output).route({"target_page": "index.html", "instruction": "Add backend validation"}).plan.status == "out_of_scope"


def test_native_pydantic_output_is_accepted():
    plan = ManagerRoutingPlan.model_validate(execute_payload())

    assert router_for(plan).route({"target_page": "index.html", "instruction": "Change button color."}).plan == plan


def test_object_pydantic_output_is_accepted():
    class Output:
        pydantic = ManagerRoutingPlan.model_validate(execute_payload())

    assert router_for(Output()).route({"target_page": "index.html", "instruction": "Change button color."}).plan.status == "execute"


def test_crewai_pydantic_wrapper_with_raw_json_is_accepted():
    class CrewOutput(BaseModel):
        raw: str
        token_usage: dict[str, int]

    output = CrewOutput(
        raw=json.dumps(execute_payload()),
        token_usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    )

    result = router_for(output).route(
        {"target_page": "index.html", "instruction": "Change button color."}
    )

    assert result.plan.status == "execute"
    assert result.token_usage.total_tokens == 5


def test_invalid_structured_output_is_rejected():
    with pytest.raises(ManagerOutputError):
        router_for(execute_payload(selected_specialists=[], assignments=[])).route(
            {"target_page": "index.html", "instruction": "Change button color."}
        )


def test_missing_structured_output_is_rejected():
    with pytest.raises(ManagerOutputError):
        router_for(object()).route({"target_page": "index.html", "instruction": "Change button color."})


def test_unknown_specialist_is_rejected():
    with pytest.raises(ManagerOutputError):
        router_for(
            execute_payload(
                selected_specialists=["content"],
                assignments=[{"agent": "content", "task": "Write copy."}],
            )
        ).route({"target_page": "index.html", "instruction": "Write copy."})


def test_qa_specialist_is_rejected():
    with pytest.raises(ManagerOutputError):
        router_for(
            execute_payload(
                selected_specialists=["qa"],
                assignments=[{"agent": "qa", "task": "Review."}],
            )
        ).route({"target_page": "index.html", "instruction": "QA this."})


def test_contradictory_plan_is_rejected():
    with pytest.raises(ManagerOutputError):
        router_for(execute_payload(clarification_question="Which page?")).route(
            {"target_page": "index.html", "instruction": "Change button color."}
        )


def test_elapsed_time_token_usage_model_and_request_are_recorded():
    class Output:
        pydantic = ManagerRoutingPlan.model_validate(execute_payload())
        token_usage = {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}

    result = router_for(Output(), clock_values=[10.0, 10.125]).route(
        {"target_page": "index.html", "instruction": "Change button color."}
    )

    assert result.latency_ms == 125.0
    assert result.token_usage.total_tokens == 5
    assert result.model == "groq/llama-test"
    assert result.request == EditRequest(target_page="index.html", instruction="Change button color.")


def test_absent_token_usage_is_allowed():
    assert router_for(execute_payload()).route({"target_page": "index.html", "instruction": "Change button color."}).token_usage.total_tokens is None


def test_no_automatic_retry_occurs():
    calls = []

    with pytest.raises(ManagerExecutionError):
        router_for(RuntimeError("first failure"), calls=calls).route(
            {"target_page": "index.html", "instruction": "Change button color."}
        )

    assert len(calls) == 1


def test_exception_does_not_contain_api_key():
    with pytest.raises(ManagerExecutionError) as error:
        router_for(RuntimeError("secret unit-test-secret leaked")).route(
            {"target_page": "index.html", "instruction": "Change button color."}
        )

    assert "unit-test-secret" not in str(error.value)


def test_invalid_edit_request_is_preserved_as_validation_error():
    with pytest.raises(ValidationError):
        router_for(execute_payload()).route({"target_page": "../index.html", "instruction": "Fix it"})
