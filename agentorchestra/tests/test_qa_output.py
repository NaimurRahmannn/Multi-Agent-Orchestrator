import pytest

from agentorchestra.exceptions import QAOutputError
from agentorchestra.models import QAResult
from agentorchestra.services.qa_output import extract_qa_result

CRITERIA = ["The requested staged edit is present."]


def accepted():
    return {
        "verdict": "accept",
        "criteria_results": [
            {
                "criterion": CRITERIA[0],
                "status": "passed",
                "evidence": "The diff shows the requested edit.",
            }
        ],
        "reason": "All criteria passed.",
    }


class Output:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_extract_qa_result_accepts_supported_shapes():
    native = QAResult.model_validate(accepted())

    assert extract_qa_result(native, acceptance_criteria=CRITERIA) == native
    assert extract_qa_result(accepted(), acceptance_criteria=CRITERIA) == native
    assert extract_qa_result(Output(pydantic=native), acceptance_criteria=CRITERIA) == native
    assert extract_qa_result(Output(json_dict=accepted()), acceptance_criteria=CRITERIA) == native
    assert extract_qa_result(Output(tasks_output=[accepted()]), acceptance_criteria=CRITERIA) == native
    assert extract_qa_result(Output(raw='```json\n{"verdict":"accept","criteria_results":[{"criterion":"The requested staged edit is present.","status":"passed","evidence":"The diff shows the requested edit."}],"reason":"All criteria passed."}\n```'), acceptance_criteria=CRITERIA) == native


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        {"verdict": "accept", "criteria_results": [], "reason": "Bad."},
        {
            "verdict": "accept",
            "criteria_results": [
                {"criterion": CRITERIA[0], "status": "failed", "evidence": "No evidence."}
            ],
            "reason": "Contradictory.",
        },
        {
            "verdict": "accept",
            "criteria_results": [
                {"criterion": "Extra criterion", "status": "passed", "evidence": "No evidence."}
            ],
            "reason": "Wrong coverage.",
        },
    ],
)
def test_extract_qa_result_rejects_invalid_output(payload):
    with pytest.raises(QAOutputError):
        extract_qa_result(payload, acceptance_criteria=CRITERIA, secrets=("qa-secret",))
