import pytest
from pydantic import ValidationError

from agentorchestra.path_safety import contains_absolute_path_text, validate_relative_site_path
from agentorchestra.pipeline_models import QAEvidenceBundle
from agentorchestra.services.qa_evidence import build_qa_evidence_bundle
from tests.test_qa_evidence import report


@pytest.mark.parametrize(
    "path",
    [
        "/home/user/site/index.html",
        "/tmp/style.css",
        "C:\\project\\index.html",
        "D:/project/style.css",
        "\\\\server\\share\\index.html",
        "../index.html",
    ],
)
def test_structured_site_paths_reject_absolute_and_traversal(path):
    with pytest.raises(ValueError):
        validate_relative_site_path(path)


@pytest.mark.parametrize("path", ["index.html", "style.css", "assets/studio-mark.svg"])
def test_structured_site_paths_accept_safe_relative_paths(path):
    assert validate_relative_site_path(path) == path


def test_free_form_slashes_are_not_mistaken_for_absolute_paths():
    values = ["</section>", "url(/assets/mark.svg)", "https://example.com/page", "a/b"]
    assert all(not contains_absolute_path_text(value) for value in values)


@pytest.mark.parametrize(
    "value",
    [
        "/home/user/site/index.html",
        "/tmp/style.css",
        "C:\\project\\index.html",
        "D:/project/style.css",
        "\\\\server\\share\\index.html",
    ],
)
def test_absolute_paths_are_detected_in_user_facing_text(value):
    assert contains_absolute_path_text(value)


def test_qa_bundle_rejects_absolute_structured_filename_but_accepts_diff_slashes():
    specialist_report = report()
    bundle = build_qa_evidence_bundle(
        request=specialist_report.request,
        plan=specialist_report.plan,
        specialist_report=specialist_report,
        diff_report=specialist_report.diff_report,
    )
    payload = bundle.model_dump(mode="json")
    payload["changed_files"] = ["/tmp/style.css"]
    payload["file_diffs"][0]["file"] = "/tmp/style.css"

    with pytest.raises(ValidationError):
        QAEvidenceBundle.model_validate(payload)

    assert "--- working/style.css" in bundle.combined_diff
