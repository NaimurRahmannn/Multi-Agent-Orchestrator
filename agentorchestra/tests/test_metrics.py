from agentorchestra.models import TokenUsage
from agentorchestra.observability_models import RunTimeline, TimelineEvent
from agentorchestra.services.metrics import build_run_metrics
from tests.test_edit_flow_cli import report


def test_metrics_derive_structured_latency_patch_diff_and_tokens():
    run_report = report("accepted")
    run_report.manager_result.token_usage = TokenUsage(
        prompt_tokens=10,
        completion_tokens=2,
        total_tokens=12,
    )
    run_report.specialist_report.results[0].token_usage = TokenUsage(
        prompt_tokens=20,
        completion_tokens=3,
        total_tokens=23,
    )
    run_report.qa_run.token_usage = TokenUsage(
        prompt_tokens=5,
        completion_tokens=1,
        total_tokens=6,
    )
    timeline = RunTimeline(
        run_id=run_report.run_id,
        events=[
            TimelineEvent(
                sequence=0,
                stage="promotion",
                status="succeeded",
                started_offset_ms=0.0,
                duration_ms=4.0,
                message="Promoted.",
            )
        ],
        total_observed_duration_ms=4.0,
    )

    metrics = build_run_metrics(run_report, timeline)

    assert metrics.applied_patch_count == 1
    assert metrics.changed_file_count == 1
    assert metrics.added_lines == 1
    assert metrics.removed_lines == 1
    assert metrics.promotion_latency_ms == 4.0
    assert metrics.known_total_tokens == 41
    assert metrics.token_usage_complete is True


def test_metrics_keep_unknown_token_aggregates_unknown():
    metrics = build_run_metrics(report("accepted"), RunTimeline())
    assert metrics.known_prompt_tokens is None
    assert metrics.known_completion_tokens is None
    assert metrics.known_total_tokens is None
    assert metrics.token_usage_complete is False
