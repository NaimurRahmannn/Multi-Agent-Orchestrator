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


def test_execute_valid_plan():
    plan = ManagerRoutingPlan.model_validate(execute_plan())

    assert plan.status == "execute"
    assert plan.selected_specialists == ["css"]


def test_execute_duplicate_specialists_rejected():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(
            execute_plan(
                selected_specialists=["css", "css"],
                assignments=[{"agent": "css", "task": "Update button color."}],
            )
        )


def test_execute_missing_assignment_rejected():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(execute_plan(assignments=[]))


def test_execute_extra_assignment_rejected():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(
            execute_plan(assignments=[{"agent": "css", "task": "Style."}, {"agent": "html", "task": "Markup."}])
        )


def test_execute_blank_criteria_rejected():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(execute_plan(acceptance_criteria=["Looks better", "  "]))


def test_unknown_qa_specialist_rejected():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(
            execute_plan(
                selected_specialists=["qa"],
                assignments=[{"agent": "qa", "task": "Review it."}],
            )
        )


def test_execute_contradictory_clarification_rejected():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(execute_plan(clarification_question="Which page?"))


def test_clarification_required_valid():
    plan = ManagerRoutingPlan.model_validate(
        {
            "status": "clarification_required",
            "request_type": "ambiguous",
            "selected_specialists": [],
            "routing_rationale": None,
            "assignments": [],
            "acceptance_criteria": [],
            "clarification_question": "Which page should change?",
            "rejection_reason": None,
        }
    )

    assert plan.clarification_question == "Which page should change?"


def test_clarification_required_rejects_execution_fields():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(
            {
                "status": "clarification_required",
                "request_type": "ambiguous",
                "selected_specialists": ["html"],
                "routing_rationale": None,
                "assignments": [{"agent": "html", "task": "Change copy."}],
                "acceptance_criteria": [],
                "clarification_question": "Which copy?",
                "rejection_reason": None,
            }
        )


def test_clarification_required_rejects_rejection_reason():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(
            {
                "status": "clarification_required",
                "request_type": "ambiguous",
                "selected_specialists": [],
                "routing_rationale": None,
                "assignments": [],
                "acceptance_criteria": [],
                "clarification_question": "Which page?",
                "rejection_reason": "Unsupported.",
            }
        )


def test_out_of_scope_valid():
    plan = ManagerRoutingPlan.model_validate(
        {
            "status": "out_of_scope",
            "request_type": "backend",
            "selected_specialists": [],
            "routing_rationale": None,
            "assignments": [],
            "acceptance_criteria": [],
            "clarification_question": None,
            "rejection_reason": "Backend work is outside Phase 1.",
        }
    )

    assert plan.rejection_reason == "Backend work is outside Phase 1."


def test_out_of_scope_rejects_clarification_question():
    with pytest.raises(ValidationError):
        ManagerRoutingPlan.model_validate(
            {
                "status": "out_of_scope",
                "request_type": "backend",
                "selected_specialists": [],
                "routing_rationale": None,
                "assignments": [],
                "acceptance_criteria": [],
                "clarification_question": "Which backend?",
                "rejection_reason": "Backend work is unsupported.",
            }
        )


def test_json_serialization_is_clean():
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

    assert decoded["status"] == "execute"
    assert decoded["selected_specialists"] == ["html", "css"]
