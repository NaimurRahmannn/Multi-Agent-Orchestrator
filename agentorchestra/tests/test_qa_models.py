import pytest
from pydantic import ValidationError

from agentorchestra.exceptions import QACoverageError
from agentorchestra.models import QAResult, validate_qa_coverage


def qa_payload(**overrides):
    payload = {
        "verdict": "accept",
        "criteria_results": [
            {
                "criterion": "Button color changes.",
                "status": "passed",
                "evidence": "The CSS diff changes the button color.",
            }
        ],
        "reason": "All criteria passed.",
    }
    payload.update(overrides)
    return payload


def test_accept_result_with_all_criteria_passed():
    result = QAResult.model_validate(qa_payload())

    assert result.verdict == "accept"


def test_reject_result_with_failed_criterion():
    result = QAResult.model_validate(
        qa_payload(
            verdict="reject",
            criteria_results=[
                {
                    "criterion": "Button color changes.",
                    "status": "failed",
                    "evidence": "The CSS diff did not change button color.",
                }
            ],
            reason="One criterion failed.",
        )
    )

    assert result.verdict == "reject"


def test_contradictory_verdicts_rejected():
    with pytest.raises(ValidationError):
        QAResult.model_validate(
            qa_payload(
                criteria_results=[
                    {
                        "criterion": "Button color changes.",
                        "status": "failed",
                        "evidence": "It failed.",
                    }
                ]
            )
        )
    with pytest.raises(ValidationError):
        QAResult.model_validate(qa_payload(verdict="reject"))


def test_empty_or_duplicate_criteria_rejected():
    with pytest.raises(ValidationError):
        QAResult.model_validate(qa_payload(criteria_results=[]))
    with pytest.raises(ValidationError):
        QAResult.model_validate(
            qa_payload(
                criteria_results=[
                    {
                        "criterion": "Button color changes.",
                        "status": "passed",
                        "evidence": "A",
                    },
                    {
                        "criterion": " button COLOR changes. ",
                        "status": "passed",
                        "evidence": "B",
                    },
                ]
            )
        )


def test_blank_evidence_reason_and_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        QAResult.model_validate(
            qa_payload(criteria_results=[{"criterion": "A", "status": "passed", "evidence": " "}])
        )
    with pytest.raises(ValidationError):
        QAResult.model_validate(qa_payload(reason=" "))
    with pytest.raises(ValidationError):
        QAResult.model_validate(qa_payload(extra="nope"))


def test_complete_qa_coverage_case_insensitive_with_original_wording():
    result = QAResult.model_validate(
        qa_payload(
            criteria_results=[
                {
                    "criterion": " button COLOR changes. ",
                    "status": "passed",
                    "evidence": "The requested color is present.",
                }
            ]
        )
    )

    validate_qa_coverage(["Button color changes."], result)
    assert result.criteria_results[0].criterion == "button COLOR changes."


def test_missing_extra_and_duplicate_qa_coverage_rejected():
    result = QAResult.model_validate(qa_payload())
    with pytest.raises(QACoverageError):
        validate_qa_coverage(["Button color changes.", "Text size changes."], result)
    with pytest.raises(QACoverageError):
        validate_qa_coverage([], result)
    with pytest.raises(QACoverageError):
        validate_qa_coverage(["Same", " same "], result)


def test_qa_result_json_round_trip():
    result = QAResult.model_validate(qa_payload())
    restored = QAResult.model_validate_json(result.model_dump_json())

    assert restored == result
