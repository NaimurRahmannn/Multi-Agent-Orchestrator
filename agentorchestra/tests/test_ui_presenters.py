from agentorchestra.models import EditRequest
from agentorchestra.pipeline_models import EditRunReport
from agentorchestra.screenshot_models import ScreenshotArtifact
from agentorchestra.services.screenshots import PNG_SIGNATURE
from agentorchestra.ui.presenters import (
    build_metric_cards,
    download_filenames,
    report_download_bytes,
    resolve_screenshot_for_display,
    sanitized_report,
)
from tests.test_edit_flow_cli import report
from tests.test_workspace_service import make_settings


def test_screenshot_resolver_returns_bytes_only_for_safe_existing_artifact(tmp_path):
    settings = make_settings(tmp_path)
    image = settings.screenshot_report_dir / "safe-run" / "before-index.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_SIGNATURE + b"image")
    artifact = ScreenshotArtifact(
        kind="before",
        status="succeeded",
        run_id="safe-run",
        target_page="index.html",
        source_site_digest="a" * 64,
        relative_path="reports/screenshots/safe-run/before-index.png",
        latency_ms=1.0,
    )
    assert resolve_screenshot_for_display(settings, artifact) == PNG_SIGNATURE + b"image"
    image.unlink()
    assert resolve_screenshot_for_display(settings, artifact) is None


def test_presenter_sanitizes_downloads_and_uses_safe_filenames(tmp_path):
    settings = make_settings(tmp_path)
    unsafe = report("failed").model_copy(
        update={
            "request": EditRequest(
                target_page="index.html",
                instruction=f"Inspect {tmp_path} using gsk_secret_value",
            )
        }
    )
    safe = sanitized_report(unsafe, settings)
    payload = report_download_bytes(unsafe, settings).decode()
    assert str(tmp_path) not in safe.request.instruction
    assert "secret_value" not in payload
    assert download_filenames(safe) == (
        "agentorchestra-no-run-index.json",
        "agentorchestra-no-run-index.diff",
    )


def test_metric_presenter_never_turns_unknown_tokens_into_zero():
    legacy = report("accepted")
    metrics = __import__(
        "agentorchestra.services.metrics", fromlist=["build_run_metrics"]
    ).build_run_metrics(legacy, legacy.timeline)
    enriched = EditRunReport.model_validate(
        {**legacy.model_dump(mode="python"), "metrics": metrics}
    )
    assert build_metric_cards(enriched)["Tokens"] == "unavailable"
