import json

import pytest
from pydantic import ValidationError

from agentorchestra.models import ManagerRoutingPlan


def execute_plan(**overrides):
    payload = {
        "status": "execute",
        "request_type": "css_change",
        "selected_specialists": ["css"],
        "routing_rationale": "This is a presentation-only change.",
        "assignments": [{"agent": "css", "task": "Update button color."}],
        "acceptance_criteria": ["Buttons use the requested color."],
        "clarification_question": None,
        "rejection_reason": None,
    }
    payload.update(overrides)
    return payload


def clarification_plan(**overrides):
    payload = {
        "status": "clarification_required",
        "request_type": "ambiguous_request",
        "selected_specialists": [],
        "routing_rationale": "The request does not name a concrete change.",
        "assignments": [],
        "acceptance_criteria": [],
        "clarification_question": "Which page and element should change?",
        "rejection_reason": None,
    }
    payload.update(overrides)
    return payload


def out_of_scope_plan(**overrides):
    payload = {
        "status": "out_of_scope",
        "request_type": "unsupported_backend",
        "selected_specialists": [],
        "routing_rationale": "Backend work is outside the static-site editing scope.",
        "assignments": [],
        "acceptance_criteria": [],
        "clarification_question": None,
        "rejection_reason": "Backend work is not supported.",
    }
    payload.update(overrides)
    return payload


def assert_invalid(payload):
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(payload)


def test_execute_valid_plan():
    plan = ManagerRoutingPlan.model_validate(execute_plan())

    assert plan.status == "execute"
    assert plan.selected_specialists == ["css"]


def test_execute_valid_multi_specialist_plan_normalizes_assignment_order():
    plan = ManagerRoutingPlan.model_validate(
        execute_plan(
            selected_specialists=["html", "css"],
            assignments=[
                {"agent": "css", "task": "Style the note."},
                {"agent": "html", "task": "Add a note."},
            ],
            acceptance_criteria=["Note exists.", "Note is styled."],
        )
    )

    assert [assignment.agent for assignment in plan.assignments] == ["html", "css"]


def test_clarification_required_valid():
    plan = ManagerRoutingPlan.model_validate(clarification_plan())

    assert plan.clarification_question == "Which page and element should change?"


def test_out_of_scope_valid():
    plan = ManagerRoutingPlan.model_validate(out_of_scope_plan())

    assert plan.rejection_reason == "Backend work is not supported."


def test_execute_requires_selected_specialists():
    assert_invalid(execute_plan(selected_specialists=[], assignments=[]))


def test_execute_duplicate_specialists_rejected():
    assert_invalid(
        execute_plan(
            selected_specialists=["css", "css"],
            assignments=[{"agent": "css", "task": "Update button color."}],
        )
    )


def test_execute_missing_assignment_rejected():
    assert_invalid(execute_plan(assignments=[]))


def test_execute_assignment_for_unselected_agent_rejected():
    assert_invalid(
        execute_plan(assignments=[{"agent": "html", "task": "Change markup."}])
    )


def test_execute_duplicate_assignment_rejected():
    assert_invalid(
        execute_plan(
            assignments=[
                {"agent": "css", "task": "Update button color."},
                {"agent": "css", "task": "Update link color."},
            ]
        )
    )


def test_blank_rationale_rejected():
    assert_invalid(execute_plan(routing_rationale=" "))


def test_blank_criteria_rejected():
    assert_invalid(execute_plan(acceptance_criteria=["Looks better", "  "]))


def test_duplicate_criteria_case_variation_rejected():
    assert_invalid(execute_plan(acceptance_criteria=["Button updated", " button UPDATED "]))


def test_clarification_rejects_specialists():
    assert_invalid(
        clarification_plan(
            selected_specialists=["html"],
            assignments=[{"agent": "html", "task": "Change copy."}],
        )
    )


def test_clarification_missing_question_rejected():
    assert_invalid(clarification_plan(clarification_question=None))


def test_out_of_scope_missing_rejection_reason_rejected():
    assert_invalid(out_of_scope_plan(rejection_reason=None))


def test_contradictory_clarification_and_rejection_fields_rejected():
    assert_invalid(clarification_plan(rejection_reason="Unsupported."))
    assert_invalid(out_of_scope_plan(clarification_question="Which backend?"))
    assert_invalid(execute_plan(clarification_question="Which page?"))
    assert_invalid(execute_plan(rejection_reason="Unsupported."))


def test_unknown_qa_specialist_rejected():
    assert_invalid(
        execute_plan(
            selected_specialists=["qa"],
            assignments=[{"agent": "qa", "task": "Review it."}],
        )
    )


def test_extra_unknown_fields_rejected():
    assert_invalid(execute_plan(extra_field="nope"))


def test_json_round_trip_is_clean():
    plan = ManagerRoutingPlan.model_validate(
        execute_plan(
            selected_specialists=["html", "css"],
            assignments=[
                {"agent": "html", "task": "Add a note."},
                {"agent": "css", "task": "Style the note."},
            ],
            acceptance_criteria=["Note exists.", "Note is styled."],
        )
    )

    encoded = plan.model_dump_json()
    decoded = json.loads(encoded)
    restored = ManagerRoutingPlan.model_validate_json(encoded)

    assert decoded["status"] == "execute"
    assert decoded["selected_specialists"] == ["html", "css"]
    assert restored == plan
