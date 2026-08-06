import pytest
from pydantic import ValidationError

from agentorchestra.screenshot_models import ScreenshotArtifact


def artifact(**updates):
    values = {
        "kind": "before",
        "status": "succeeded",
        "run_id": "safe-run",
        "target_page": "index.html",
        "source_site_digest": "a" * 64,
        "relative_path": "reports/screenshots/safe-run/before-index.png",
        "viewport_width": 1440,
        "viewport_height": 900,
        "full_page": True,
        "latency_ms": 2.0,
    }
    values.update(updates)
    return ScreenshotArtifact(**values)


def test_screenshot_artifact_round_trip_and_status_contracts():
    succeeded = artifact()
    assert ScreenshotArtifact.model_validate_json(succeeded.model_dump_json()) == succeeded
    failed = artifact(status="failed", relative_path=None, error="Chromium unavailable.")
    skipped = artifact(
        status="skipped",
        relative_path=None,
        source_site_digest=None,
        warnings=["Diagnostic capture skipped."],
    )
    assert failed.error
    assert skipped.relative_path is None


@pytest.mark.parametrize(
    "updates",
    [
        {"run_id": "../escape"},
        {"target_page": "nested/index.html"},
        {"relative_path": "reports/other/image.png"},
        {"relative_path": "C:/private/image.png"},
        {"status": "succeeded", "relative_path": None},
        {"status": "failed", "relative_path": None, "error": None},
        {"status": "skipped", "relative_path": None, "error": "unexpected"},
        {"viewport_width": 0},
        {"error": "gsk_secret"},
        {"unknown": True},
    ],
)
def test_screenshot_artifact_rejects_unsafe_or_inconsistent_values(updates):
    with pytest.raises(ValidationError):
        artifact(**updates)
