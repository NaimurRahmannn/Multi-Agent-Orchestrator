from agentorchestra.exceptions import ScreenshotSafetyError
from agentorchestra.flow import AgentOrchestraFlow
from agentorchestra.screenshot_models import ScreenshotArtifact
from tests.specialist_helpers import execute_plan
from tests.test_edit_flow import FakeQA, FakeRouter, FakeSpecialists
from tests.test_seo_flow import SEOService, diagnostic_plan, successful_lighthouse
from tests.test_workspace_service import make_settings


def successful_capture(**kwargs):
    return ScreenshotArtifact(
        kind=kwargs["kind"],
        status="succeeded",
        run_id=kwargs["run_id"],
        target_page=kwargs["target_page"],
        source_site_digest=kwargs["source_site_digest"],
        relative_path=(
            f"reports/screenshots/{kwargs['run_id']}/"
            f"{kwargs['kind'].value.replace('_', '-')}-index.png"
        ),
        latency_ms=2.0,
    )


def test_accepted_flow_records_real_visual_order_timeline_and_metrics(tmp_path):
    settings = make_settings(tmp_path)
    specialists = FakeSpecialists(("succeeded",))
    specialists.settings = settings
    calls = []

    def capture(**kwargs):
        calls.append(kwargs)
        return successful_capture(**kwargs)

    report = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(execute_plan("css")),
        specialist_service=specialists,
        qa_runner=FakeQA("accept"),
        screenshot_capture=capture,
    ).kickoff(inputs={"request": {"target_page": "index.html", "instruction": "Change CSS."}})

    assert report.status == "accepted", report.error
    assert [call["kind"].value for call in calls] == ["before", "proposed_after"]
    assert calls[0]["site_root"] == settings.working_site_dir
    assert calls[1]["site_root"].parent == settings.staging_root_dir
    assert calls[1]["source_site_digest"] == report.accepted_content_digest
    assert [event.stage.value for event in report.timeline.events] == [
        "manager",
        "workspace",
        "screenshot_before",
        "specialist_css",
        "evidence_validation",
        "screenshot_proposed_after",
        "qa",
        "promotion",
    ]
    assert report.metrics.applied_patch_count == 1
    assert report.metrics.changed_file_count == 1
    assert report.metrics.screenshot_latency_ms == 4.0


def test_failed_screenshot_is_warning_and_does_not_change_acceptance(tmp_path):
    settings = make_settings(tmp_path)
    specialists = FakeSpecialists(("succeeded",))
    specialists.settings = settings

    def capture(**kwargs):
        return ScreenshotArtifact(
            kind=kwargs["kind"],
            status="failed",
            run_id=kwargs["run_id"],
            target_page=kwargs["target_page"],
            source_site_digest=kwargs["source_site_digest"],
            latency_ms=1.0,
            error="Chromium unavailable.",
        )

    report = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(execute_plan("css")),
        specialist_service=specialists,
        qa_runner=FakeQA("accept"),
        screenshot_capture=capture,
    ).kickoff(inputs={"request": {"target_page": "index.html", "instruction": "Change CSS."}})

    assert report.status == "accepted", report.error
    assert len(report.screenshots) == 2
    assert all(item.status.value == "failed" for item in report.screenshots)
    assert any("screenshot unavailable" in warning for warning in report.warnings)


def test_screenshot_safety_exception_fails_before_specialists(tmp_path):
    settings = make_settings(tmp_path)
    specialists = FakeSpecialists(("succeeded",))
    specialists.settings = settings

    def unsafe(**kwargs):
        del kwargs
        raise ScreenshotSafetyError("Unsafe screenshot root.")

    report = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(execute_plan("css")),
        specialist_service=specialists,
        qa_runner=FakeQA("accept"),
        screenshot_capture=unsafe,
    ).kickoff(inputs={"request": {"target_page": "index.html", "instruction": "Change CSS."}})

    assert report.status == "failed"
    assert specialists.calls == []
    assert report.staging_cleaned is True


def test_diagnostic_records_skipped_visual_evidence_without_browser_or_qa(tmp_path):
    settings = make_settings(tmp_path)
    calls = []

    def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("browser must not run")

    report = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(diagnostic_plan()),
        specialist_service=SEOService(settings, diagnostic=True),
        qa_runner=FakeQA(),
        lighthouse_runner=successful_lighthouse,
        screenshot_capture=forbidden,
    ).kickoff(
        inputs={"request": {"target_page": "index.html", "instruction": "Diagnose SEO."}}
    )

    assert report.status == "diagnostic_completed"
    assert calls == []
    assert report.screenshots[0].status.value == "skipped"
    assert report.qa_run is None
    assert report.metrics.seo_latency_ms > 0
    assert report.metrics.lighthouse_latency_ms == 5.0
