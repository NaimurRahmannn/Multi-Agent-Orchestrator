import pytest
from pydantic import ValidationError

from agentorchestra.observability_models import RunTimeline, TimelineEvent
from agentorchestra.services.timeline import RunTimelineRecorder
from tests.test_workspace_service import make_settings


def test_recorder_preserves_sequence_duration_and_immutable_snapshots(tmp_path):
    settings = make_settings(tmp_path)
    values = iter([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    recorder = RunTimelineRecorder(clock=values.__next__, settings=settings)
    first = recorder.start("manager")
    recorder.finish(first, status="succeeded", message="Manager completed.")
    snapshot = recorder.snapshot()
    recorder.record("workspace", status="succeeded", message="Workspace ready.")

    assert [event.sequence for event in snapshot.events] == [0]
    assert [event.stage.value for event in recorder.snapshot().events] == ["manager", "workspace"]
    assert snapshot.events[0].duration_ms == pytest.approx(100.0)


def test_recorder_redacts_paths_and_keys(tmp_path):
    settings = make_settings(tmp_path)
    recorder = RunTimelineRecorder(settings=settings)
    event = recorder.record(
        "manager",
        status="failed",
        message=f"failed at {tmp_path} with gsk_super_secret",
    )
    assert str(tmp_path) not in event.message
    assert "super_secret" not in event.message


def test_timeline_rejects_invalid_order_and_specialist_mismatch():
    event = TimelineEvent(
        sequence=1,
        stage="manager",
        status="succeeded",
        started_offset_ms=0.0,
        duration_ms=1.0,
        message="Done.",
    )
    with pytest.raises(ValidationError):
        RunTimeline(events=[event, event], total_observed_duration_ms=2.0)
    with pytest.raises(ValidationError):
        TimelineEvent(
            sequence=0,
            stage="specialist_css",
            specialist="html",
            status="succeeded",
            started_offset_ms=0.0,
            duration_ms=1.0,
            message="Done.",
        )
