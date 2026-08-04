import json

import pytest
from pydantic import ValidationError

from agentorchestra.models import PatchProposal


def patch_payload(**overrides):
    payload = {
        "agent": "css",
        "file": "style.css",
        "old_text": "color: teal;",
        "new_text": "color: navy;",
        "summary": "Update color.",
    }
    payload.update(overrides)
    return payload


def assert_invalid(**overrides):
    with pytest.raises(ValidationError):
        PatchProposal.model_validate(patch_payload(**overrides))


def test_valid_css_patch():
    patch = PatchProposal.model_validate(patch_payload())

    assert patch.agent == "css"


def test_valid_html_patch():
    patch = PatchProposal.model_validate(
        patch_payload(agent="html", file="index.html", old_text="<h1>Old</h1>", new_text="<h1>New</h1>")
    )

    assert patch.file == "index.html"


def test_valid_seo_html_patch():
    patch = PatchProposal.model_validate(
        patch_payload(agent="seo", file="about.html", old_text="<title>Old</title>", new_text="<title>New</title>")
    )

    assert patch.agent == "seo"


def test_agent_file_ownership_is_enforced():
    assert_invalid(agent="css", file="index.html")
    assert_invalid(agent="html", file="style.css")
    assert_invalid(agent="seo", file="style.css")


@pytest.mark.parametrize("file", ["script.js", "/tmp/style.css", "../style.css", "assets/style.css"])
def test_unsafe_or_unsupported_file_rejected(file):
    assert_invalid(file=file)


def test_empty_patch_text_rejected():
    assert_invalid(old_text="")
    assert_invalid(new_text="")


def test_identical_old_and_new_text_rejected():
    assert_invalid(old_text="same", new_text="same")


def test_null_bytes_rejected():
    assert_invalid(old_text="bad\x00text")
    assert_invalid(new_text="bad\x00text")
    assert_invalid(file="style.css\x00")


def test_patch_text_indentation_is_preserved():
    patch = PatchProposal.model_validate(
        patch_payload(old_text="  color: teal;\n", new_text="    color: navy;\n")
    )

    assert patch.old_text == "  color: teal;\n"
    assert patch.new_text == "    color: navy;\n"


def test_excessive_patch_size_rejected():
    assert_invalid(old_text="x" * 5_001)
    assert_invalid(new_text="x" * 5_001)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        PatchProposal.model_validate(patch_payload(confidence=0.9))


def test_json_round_trip():
    patch = PatchProposal.model_validate(patch_payload())
    restored = PatchProposal.model_validate_json(patch.model_dump_json())

    assert json.loads(patch.model_dump_json())["agent"] == "css"
    assert restored == patch
