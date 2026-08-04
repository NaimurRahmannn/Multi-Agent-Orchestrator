import pytest
from pydantic import ValidationError

from agentorchestra.models import EditRequest


def test_valid_index_page():
    request = EditRequest(target_page="index.html", instruction="Change the headline.")

    assert request.target_page == "index.html"


def test_trims_target_page_and_instruction():
    request = EditRequest(target_page=" about.html ", instruction="  Update copy.  ")

    assert request.target_page == "about.html"
    assert request.instruction == "Update copy."


@pytest.mark.parametrize(
    ("target_page", "instruction"),
    [
        ("index.html", ""),
        ("index.html", "   "),
        ("/tmp/index.html", "Update."),
        ("../index.html", "Update."),
        ("pages/index.html", "Update."),
        ("pages\\index.html", "Update."),
        ("style.css", "Update."),
        ("index.html\x00", "Update."),
        (".index.html", "Update."),
    ],
)
def test_invalid_edit_request_values(target_page, instruction):
    with pytest.raises(ValidationError):
        EditRequest(target_page=target_page, instruction=instruction)


def test_excessive_instruction_size_rejected():
    with pytest.raises(ValidationError):
        EditRequest(target_page="contact.html", instruction="x" * 2_001)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        EditRequest(target_page="index.html", instruction="Update.", session_id="abc")


def test_json_round_trip():
    request = EditRequest(target_page="contact.html", instruction="Update the form heading.")
    restored = EditRequest.model_validate_json(request.model_dump_json())

    assert restored == request
