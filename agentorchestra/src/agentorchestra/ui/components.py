from __future__ import annotations

import streamlit as st

from agentorchestra.config import Settings
from agentorchestra.pipeline_models import EditOutcomeStatus, EditRunReport
from agentorchestra.screenshot_models import ScreenshotKind, ScreenshotStatus
from agentorchestra.ui.presenters import (
    build_metric_cards,
    build_patch_rows,
    build_qa_rows,
    build_specialist_rows,
    build_timeline_rows,
    diff_download_bytes,
    download_filenames,
    outcome_label,
    report_download_bytes,
    resolve_screenshot_for_display,
)


def render_report(report: EditRunReport, settings: Settings) -> None:
    _render_outcome(report)
    _render_routing(report)
    _render_timeline(report)
    _render_screenshots(report, settings)
    _render_specialists(report)
    _render_diff(report)
    _render_lighthouse(report)
    _render_qa(report)
    _render_metrics(report)
    _render_warnings(report)
    report_name, diff_name = download_filenames(report)
    left, right = st.columns(2)
    left.download_button(
        "Download report JSON",
        data=report_download_bytes(report, settings),
        file_name=report_name,
        mime="application/json",
    )
    right.download_button(
        "Download unified diff",
        data=diff_download_bytes(report),
        file_name=diff_name,
        mime="text/plain",
    )


def _render_outcome(report: EditRunReport) -> None:
    label = outcome_label(report.status)
    text = f"{label}: {report.message}"
    if report.status is EditOutcomeStatus.ACCEPTED:
        st.success(text)
        st.caption("Working site updated.")
    elif report.status in {EditOutcomeStatus.REJECTED, EditOutcomeStatus.BLOCKED}:
        st.warning(text)
    elif report.status in {
        EditOutcomeStatus.CLARIFICATION_REQUIRED,
        EditOutcomeStatus.OUT_OF_SCOPE,
        EditOutcomeStatus.DIAGNOSTIC_COMPLETED,
    }:
        st.info(text)
    else:
        st.error(text)


def _render_routing(report: EditRunReport) -> None:
    st.subheader("Request and routing")
    st.write({"target_page": report.request.target_page, "instruction": report.request.instruction})
    if report.plan is None:
        st.caption("Manager routing did not complete.")
        return
    st.write(
        {
            "manager_status": report.plan.status.value,
            "request_type": report.plan.request_type,
            "selected_specialists": [item.value for item in report.plan.selected_specialists],
            "routing_rationale": report.plan.routing_rationale,
            "assignments": [item.model_dump(mode="json") for item in report.plan.assignments],
            "acceptance_criteria": report.plan.acceptance_criteria,
        }
    )


def _render_timeline(report: EditRunReport) -> None:
    st.subheader("Execution timeline")
    rows = build_timeline_rows(report)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No timeline evidence is available for this legacy report.")


def _render_screenshots(report: EditRunReport, settings: Settings) -> None:
    st.subheader("Visual evidence")
    by_kind = {item.kind: item for item in report.screenshots}
    left, right = st.columns(2)
    _render_one_screenshot(left, "Before", by_kind.get(ScreenshotKind.BEFORE), settings)
    proposed_label = (
        "After — applied"
        if report.status is EditOutcomeStatus.ACCEPTED
        else "Proposed result — not applied"
    )
    _render_one_screenshot(
        right,
        proposed_label,
        by_kind.get(ScreenshotKind.PROPOSED_AFTER),
        settings,
    )


def _render_one_screenshot(column, label, artifact, settings) -> None:
    column.markdown(f"#### {label}")
    if artifact is None:
        column.caption("No screenshot was produced.")
        return
    if artifact.status is ScreenshotStatus.SUCCEEDED:
        image = resolve_screenshot_for_display(settings, artifact)
        if image is not None:
            column.image(image, use_container_width=True)
        else:
            column.warning("The screenshot artifact is no longer safe or available.")
    elif artifact.status is ScreenshotStatus.FAILED:
        column.warning(artifact.error or "Screenshot capture failed.")
    else:
        column.caption(artifact.warnings[0] if artifact.warnings else "Screenshot skipped.")


def _render_specialists(report: EditRunReport) -> None:
    st.subheader("Specialist execution")
    rows = build_specialist_rows(report)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No specialists ran.")
    patches = build_patch_rows(report)
    if patches:
        st.markdown("#### Patch evidence")
        st.dataframe(patches, use_container_width=True, hide_index=True)
    if report.seo_diagnostic_report is not None:
        st.markdown("#### SEO findings")
        st.dataframe(
            [item.model_dump(mode="json") for item in report.seo_diagnostic_report.findings],
            use_container_width=True,
            hide_index=True,
        )


def _render_diff(report: EditRunReport) -> None:
    st.subheader("Deterministic diff")
    diff = report.reviewed_diff
    with st.expander("View unified diff", expanded=False):
        st.code(diff.combined_diff if diff is not None else "(no diff)", language="diff")


def _render_lighthouse(report: EditRunReport) -> None:
    st.subheader("Lighthouse SEO")
    audit = report.lighthouse_seo
    if audit is None:
        st.caption("Lighthouse did not run because SEO was not selected or execution stopped earlier.")
        return
    if audit.score is not None:
        st.metric("SEO score", audit.score)
    st.write(
        {
            "failed_audit_ids": audit.failed_audit_ids,
            "report_path": audit.report_path or "unavailable",
            "latency_ms": round(audit.latency_ms, 1),
        }
    )
    if audit.audits:
        st.dataframe(
            [item.model_dump(mode="json") for item in audit.audits],
            use_container_width=True,
            hide_index=True,
        )
    if audit.error:
        st.warning(audit.error)


def _render_qa(report: EditRunReport) -> None:
    st.subheader("QA")
    if report.status is EditOutcomeStatus.DIAGNOSTIC_COMPLETED:
        st.info("QA did not run because diagnostic requests do not modify working.")
        return
    if report.qa_run is None:
        st.caption("QA did not run.")
        return
    st.write(
        {
            "verdict": report.qa_run.result.verdict.value,
            "latency_ms": round(report.qa_run.latency_ms, 1),
            "token_usage": report.qa_run.token_usage.model_dump(mode="json"),
        }
    )
    st.dataframe(build_qa_rows(report), use_container_width=True, hide_index=True)


def _render_metrics(report: EditRunReport) -> None:
    st.subheader("Run metrics")
    cards = build_metric_cards(report)
    if not cards:
        st.caption("Metrics are unavailable for this legacy report.")
        return
    columns = st.columns(3)
    for index, (label, value) in enumerate(cards.items()):
        columns[index % 3].metric(label, value)


def _render_warnings(report: EditRunReport) -> None:
    warnings = list(dict.fromkeys([*report.warnings, *report.cleanup_warnings]))
    if report.metrics is not None and not report.metrics.token_usage_complete:
        warnings.append("Some executed-agent token metadata is unavailable.")
    if warnings:
        st.subheader("Warnings")
        for warning in dict.fromkeys(warnings):
            st.warning(warning)
