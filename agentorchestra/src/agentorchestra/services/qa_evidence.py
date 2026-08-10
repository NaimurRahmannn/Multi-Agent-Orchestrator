from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from agentorchestra.exceptions import ExecutionEvidenceError
from agentorchestra.models import EditRequest, ManagerRoutingPlan, RoutingStatus, SpecialistName
from agentorchestra.path_safety import (
    contains_absolute_path_text,
    validate_relative_site_path,
)
from agentorchestra.pipeline_models import QAEvidenceBundle, QAPatchEvidence, QASpecialistEvidence
from agentorchestra.seo_models import LighthouseSEOResult, SEOCompletion, SEOExecutionMode
from agentorchestra.specialist_models import (
    SpecialistExecutionReport,
    SpecialistExecutionStatus,
    SpecialistRunStatus,
)
from agentorchestra.workspace_models import DiffReport, PatchExecutionResult, PatchStatus


def validate_execution_evidence(
    plan: ManagerRoutingPlan,
    specialist_report: SpecialistExecutionReport,
    diff_report: DiffReport,
) -> None:
    """Reject inconsistent staged-edit evidence before QA sees it."""
    if plan.status is not RoutingStatus.EXECUTE:
        raise ExecutionEvidenceError("QA evidence requires an execute Manager plan.")
    if not plan.acceptance_criteria:
        raise ExecutionEvidenceError("QA evidence requires Manager acceptance criteria.")
    selected = list(plan.selected_specialists)
    if any(
        specialist not in {SpecialistName.HTML, SpecialistName.CSS, SpecialistName.SEO}
        for specialist in selected
    ):
        raise ExecutionEvidenceError("QA evidence contains an unsupported specialist.")
    if specialist_report.seo_mode is SEOExecutionMode.DIAGNOSTIC:
        raise ExecutionEvidenceError("SEO diagnostic runs do not enter edit QA.")
    if specialist_report.status is not SpecialistExecutionStatus.SUCCEEDED:
        raise ExecutionEvidenceError("QA requires fully successful specialist execution.")
    if specialist_report.run_id != diff_report.run_id:
        raise ExecutionEvidenceError("Diff run ID does not match specialist execution run ID.")
    if specialist_report.diff_report != diff_report:
        raise ExecutionEvidenceError("Final diff does not match specialist execution evidence.")
    if diff_report.is_empty or not diff_report.changed_files:
        raise ExecutionEvidenceError("QA evidence requires a non-empty final diff.")

    results = specialist_report.results
    result_specialists = [result.specialist for result in results]
    if result_specialists != selected:
        raise ExecutionEvidenceError("Specialist results must exactly match selected order.")
    if any(
        result.status
        not in {SpecialistRunStatus.SUCCEEDED, SpecialistRunStatus.ALREADY_SATISFIED}
        for result in results
    ):
        raise ExecutionEvidenceError("Every selected specialist must succeed before QA.")

    applied = [
        patch
        for result in results
        for patch in result.patch_results
        if patch.status is PatchStatus.APPLIED
    ]
    if not applied:
        raise ExecutionEvidenceError("QA evidence requires at least one applied patch.")

    changed_files = set(diff_report.changed_files)
    applied_files = {patch.file for patch in applied}
    if changed_files != applied_files:
        raise ExecutionEvidenceError("Changed files and applied patch files must match exactly.")
    if _contains_asset(diff_report.changed_files):
        raise ExecutionEvidenceError("QA evidence must not include asset changes.")
    for file in diff_report.changed_files:
        _validate_evidence_path(file)
    for file_diff in diff_report.files:
        _validate_evidence_path(file_diff.file)

    for result in results:
        if any(not change.source_verified for change in result.style_changes):
            raise ExecutionEvidenceError("CSS semantic evidence must be source verified.")
        _validate_result_ownership(
            result.specialist,
            result.patch_results,
            specialist_report.request.target_page,
        )
    for result in results:
        for patch in result.patch_results:
            _validate_evidence_path(patch.file)
            _reject_absolute_path_text(patch.summary)
            _reject_absolute_path_text(patch.message)


def build_qa_evidence_bundle(
    *,
    request: EditRequest,
    plan: ManagerRoutingPlan,
    specialist_report: SpecialistExecutionReport,
    diff_report: DiffReport,
    site_content_digest: str | None = None,
    lighthouse_seo: LighthouseSEOResult | None = None,
) -> QAEvidenceBundle:
    """Build stable, prompt-safe QA evidence from runtime objects only."""
    validate_execution_evidence(plan, specialist_report, diff_report)
    file_diffs = [
        {
            "file": file_diff.file,
            "change_type": file_diff.change_type,
            "unified_diff": file_diff.unified_diff,
            "added_lines": file_diff.added_lines,
            "removed_lines": file_diff.removed_lines,
        }
        for file_diff in diff_report.files
    ]
    specialist_results = []
    for result in specialist_report.results:
        seo_completion = result.completion if isinstance(result.completion, SEOCompletion) else None
        specialist_results.append(
            QASpecialistEvidence(
                specialist=result.specialist,
                assignment=result.assignment,
                runtime_status=result.status.value,
                completion_status=result.completion.status.value if result.completion else None,
                completion_summary=result.completion.summary if result.completion else None,
                changed_files=result.changed_files,
                applied_patch_count=result.applied_patch_count,
                rejected_patch_count=result.rejected_patch_count,
                patch_results=[_patch_evidence(patch) for patch in result.patch_results],
                seo_mode=result.mode if result.specialist is SpecialistName.SEO else None,
                seo_findings=seo_completion.findings if seo_completion is not None else [],
                style_changes=result.style_changes,
            )
        )
    payload = {
        "request": request.model_dump(mode="json"),
        "run_id": diff_report.run_id,
        "target_page": request.target_page,
        "selected_specialists": [specialist.value for specialist in plan.selected_specialists],
        "assignments": [assignment.model_dump(mode="json") for assignment in plan.assignments],
        "acceptance_criteria": list(plan.acceptance_criteria),
        "specialist_results": [result.model_dump(mode="json") for result in specialist_results],
        "changed_files": list(diff_report.changed_files),
        "file_diffs": file_diffs,
        "combined_diff": diff_report.combined_diff,
        "total_added_lines": diff_report.total_added_lines,
        "total_removed_lines": diff_report.total_removed_lines,
        "site_content_digest": site_content_digest,
        "lighthouse_seo": (
            lighthouse_seo.model_dump(mode="json") if lighthouse_seo is not None else None
        ),
    }
    digest = _stable_digest(payload)
    return QAEvidenceBundle(**payload, evidence_digest=digest)


def _patch_evidence(patch: PatchExecutionResult) -> QAPatchEvidence:
    return QAPatchEvidence(
        file=patch.file,
        specialist=patch.specialist,
        status=patch.status.value,
        summary=patch.summary,
        match_count=patch.match_count,
        replacements=patch.replacements,
        before_sha256=patch.before_sha256,
        after_sha256=patch.after_sha256,
        rejection_reason=patch.rejection_reason.value if patch.rejection_reason else None,
        message=patch.message,
    )


def _validate_result_ownership(
    specialist: SpecialistName,
    patches: Iterable[PatchExecutionResult],
    target_page: str,
) -> None:
    for patch in patches:
        if patch.status is not PatchStatus.APPLIED:
            continue
        if specialist in {SpecialistName.HTML, SpecialistName.SEO} and patch.file != target_page:
            raise ExecutionEvidenceError(
                f"{specialist.value.upper()} applied patch must target the selected page only."
            )
        if specialist is SpecialistName.CSS and patch.file != "style.css":
            raise ExecutionEvidenceError("CSS applied patch must target style.css only.")
        if patch.specialist != specialist.value:
            raise ExecutionEvidenceError("Applied patch specialist must match run specialist.")


def _contains_asset(files: Iterable[str]) -> bool:
    return any(file.startswith("assets/") or "/" in file or "\\" in file for file in files)


def _stable_digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reject_absolute_path_text(text: str) -> None:
    if contains_absolute_path_text(text):
        raise ExecutionEvidenceError("QA evidence must not include absolute paths.")


def _validate_evidence_path(value: str) -> None:
    try:
        validate_relative_site_path(value)
    except ValueError as exc:
        raise ExecutionEvidenceError("QA evidence paths must be safe and relative.") from exc
