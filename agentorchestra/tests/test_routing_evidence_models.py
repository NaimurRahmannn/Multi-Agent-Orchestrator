import pytest
from pydantic import ValidationError

from agentorchestra.models import (
    ManagerRoutingPlan,
    RoutingEvidenceCase,
    RoutingEvidenceResult,
    TokenUsage,
    evaluate_routing_match,
)


def execute_case(**overrides):
    payload = {
        "case_id": "css_case",
        "request": "Change button color.",
        "expected_status": "execute",
        "expected_specialists": ["css"],
    }
    payload.update(overrides)
    return payload


def execute_plan(**overrides):
    payload = {
        "status": "execute",
        "request_type": "css_change",
        "selected_specialists": ["css"],
        "routing_rationale": "CSS owns presentation changes.",
        "assignments": [{"agent": "css", "task": "Change button color."}],
        "acceptance_criteria": ["Button color changes."],
        "clarification_question": None,
        "rejection_reason": None,
    }
    payload.update(overrides)
    return ManagerRoutingPlan.model_validate(payload)


def result_payload(**overrides):
    payload = {
        "case_id": "css_case",
        "request": "Change button color.",
        "expected_status": "execute",
        "expected_specialists": ["css"],
        "actual_status": "execute",
        "actual_specialists": ["css"],
        "routing_rationale": "CSS owns presentation changes.",
        "structurally_valid": True,
        "routing_correct": True,
        "latency_ms": 12,
        "token_usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        "validation_error": None,
    }
    payload.update(overrides)
    return payload


def test_valid_execute_case():
    case = RoutingEvidenceCase.model_validate(execute_case())

    assert case.expected_specialists == ["css"]


def test_valid_clarification_case():
    case = RoutingEvidenceCase.model_validate(
        execute_case(
            case_id="ambiguous",
            expected_status="clarification_required",
            expected_specialists=[],
        )
    )

    assert case.expected_status == "clarification_required"


def test_invalid_execute_case_without_specialists():
    with pytest.raises(ValidationError):
        RoutingEvidenceCase.model_validate(execute_case(expected_specialists=[]))


def test_invalid_non_execute_case_with_specialists():
    with pytest.raises(ValidationError):
        RoutingEvidenceCase.model_validate(
            execute_case(expected_status="out_of_scope", expected_specialists=["seo"])
        )


def test_duplicate_expected_specialists_rejected():
    with pytest.raises(ValidationError):
        RoutingEvidenceCase.model_validate(execute_case(expected_specialists=["css", "css"]))


def test_token_values_cannot_be_negative():
    with pytest.raises(ValidationError):
        TokenUsage(prompt_tokens=-1)


def test_valid_token_total_and_unknown_values():
    assert TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5).total_tokens == 5
    assert TokenUsage().model_dump(mode="json") == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def test_inconsistent_token_total_rejected():
    with pytest.raises(ValidationError):
        TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=6)


def test_invalid_structural_result_requires_validation_error():
    with pytest.raises(ValidationError):
        RoutingEvidenceResult.model_validate(
            result_payload(structurally_valid=False, routing_correct=False, validation_error=None)
        )


def test_valid_structural_result_rejects_validation_error():
    with pytest.raises(ValidationError):
        RoutingEvidenceResult.model_validate(result_payload(validation_error="bad"))


def test_invalid_structure_cannot_be_routing_correct():
    with pytest.raises(ValidationError):
        RoutingEvidenceResult.model_validate(
            result_payload(
                actual_status=None,
                actual_specialists=[],
                structurally_valid=False,
                routing_correct=True,
                validation_error="bad",
            )
        )


def test_evaluate_routing_match_exact_and_order_insensitive():
    case = RoutingEvidenceCase.model_validate(
        execute_case(expected_specialists=["html", "css"])
    )
    plan = execute_plan(
        selected_specialists=["css", "html"],
        assignments=[
            {"agent": "html", "task": "Add note."},
            {"agent": "css", "task": "Style note."},
        ],
        acceptance_criteria=["Note exists."],
    )

    assert evaluate_routing_match(case, plan)


def test_evaluate_routing_match_wrong_status_or_specialists():
    case = RoutingEvidenceCase.model_validate(execute_case())
    out_of_scope_plan = ManagerRoutingPlan.model_validate(
        {
            "status": "out_of_scope",
            "request_type": "unsupported_backend",
            "selected_specialists": [],
            "routing_rationale": "Backend work is unsupported.",
            "assignments": [],
            "acceptance_criteria": [],
            "clarification_question": None,
            "rejection_reason": "Backend work is unsupported.",
        }
    )

    assert not evaluate_routing_match(case, out_of_scope_plan)
    assert not evaluate_routing_match(
        case,
        execute_plan(
            selected_specialists=["html"],
            assignments=[{"agent": "html", "task": "Change markup."}],
        ),
    )


def test_routing_result_json_round_trip():
    result = RoutingEvidenceResult.model_validate(result_payload())
    restored = RoutingEvidenceResult.model_validate_json(result.model_dump_json())

    assert restored == result
