from __future__ import annotations

from agentorchestra.models import (
    CSS_EDIT_REQUEST_TYPE,
    HTML_CSS_EDIT_REQUEST_TYPE,
    HTML_EDIT_REQUEST_TYPE,
    SEO_EDIT_REQUEST_TYPE,
    EditRequest,
    ManagerRoutingPlan,
    SpecialistAssignment,
    SpecialistName,
    TokenUsage,
)
from agentorchestra.seo_models import SEOCompletion, SEOExecutionMode
from agentorchestra.specialist_models import (
    SpecialistCompletion,
    SpecialistRunResult,
    SpecialistRunStatus,
)
from agentorchestra.style_models import StyleChangeEvidence
from agentorchestra.workspace_models import (
    DiffReport,
    PatchExecutionResult,
    PatchRejectionReason,
    PatchStatus,
)


def applied_patch(file: str = "style.css", specialist: str = "css") -> PatchExecutionResult:
    return PatchExecutionResult(
        status=PatchStatus.APPLIED,
        file=file,
        specialist=specialist,
        summary="Apply requested edit.",
        match_count=1,
        replacements=1,
        bytes_before=10,
        bytes_after=11,
        before_sha256="a" * 64,
        after_sha256="b" * 64,
        rejection_reason=None,
        message="Patch applied atomically to the staged file.",
    )


def rejected_patch(
    reason: PatchRejectionReason = PatchRejectionReason.TARGET_NOT_FOUND,
    *,
    file: str = "style.css",
    specialist: str = "css",
) -> PatchExecutionResult:
    return PatchExecutionResult(
        status=PatchStatus.REJECTED,
        file=file,
        specialist=specialist,
        summary="Rejected requested edit.",
        match_count=0,
        replacements=0,
        rejection_reason=reason,
        message="Patch was rejected safely.",
    )


def execute_plan(*specialists: SpecialistName | str) -> ManagerRoutingPlan:
    selected = [SpecialistName(item) for item in specialists] or [SpecialistName.CSS]
    selected_set = set(selected)
    request_type = (
        SEO_EDIT_REQUEST_TYPE
        if SpecialistName.SEO in selected_set
        else HTML_CSS_EDIT_REQUEST_TYPE
        if selected_set == {SpecialistName.HTML, SpecialistName.CSS}
        else HTML_EDIT_REQUEST_TYPE
        if selected_set == {SpecialistName.HTML}
        else CSS_EDIT_REQUEST_TYPE
    )
    return ManagerRoutingPlan(
        status="execute",
        request_type=request_type,
        selected_specialists=selected,
        routing_rationale="Each task follows specialist ownership.",
        assignments=[
            SpecialistAssignment(agent=specialist, task=f"Perform {specialist.value} edit.")
            for specialist in selected
        ],
        acceptance_criteria=["The requested staged edit is present."],
        clarification_question=None,
        rejection_reason=None,
    )


def run_result(
    specialist: SpecialistName | str = SpecialistName.CSS,
    status: SpecialistRunStatus | str = SpecialistRunStatus.SUCCEEDED,
    *,
    assignment: str | None = None,
    patches: list[PatchExecutionResult] | None = None,
) -> SpecialistRunResult:
    specialist = SpecialistName(specialist)
    status = SpecialistRunStatus(status)
    if patches is None:
        patches = (
            [applied_patch("index.html", "html")]
            if specialist is SpecialistName.HTML and status is SpecialistRunStatus.SUCCEEDED
            else [applied_patch("index.html", "seo")]
            if specialist is SpecialistName.SEO and status is SpecialistRunStatus.SUCCEEDED
            else [applied_patch()]
            if status is SpecialistRunStatus.SUCCEEDED
            else []
        )
    completion_status = (
        "completed"
        if status is SpecialistRunStatus.SUCCEEDED
        else "already_satisfied"
        if status is SpecialistRunStatus.ALREADY_SATISFIED
        else "clarification_required"
        if status is SpecialistRunStatus.CLARIFICATION_REQUIRED
        else "blocked"
    )
    completion = (
        SEOCompletion(
            mode=SEOExecutionMode.EDIT,
            status="completed" if status is SpecialistRunStatus.SUCCEEDED else "blocked",
            summary=(
                "Specialist completed safely."
                if status is SpecialistRunStatus.SUCCEEDED
                else "Specialist was blocked."
            ),
            remaining_issue=(
                None if status is SpecialistRunStatus.SUCCEEDED else "No safe patch was available."
            ),
        )
        if specialist is SpecialistName.SEO
        else SpecialistCompletion(
            status=completion_status,
            summary=(
                "Specialist completed safely."
                if status is SpecialistRunStatus.SUCCEEDED
                else "The requested style is already present."
                if status is SpecialistRunStatus.ALREADY_SATISFIED
                else "The requested target is ambiguous."
                if status is SpecialistRunStatus.CLARIFICATION_REQUIRED
                else "Specialist was blocked."
            ),
            remaining_issue=(
                "No safe patch was available."
                if status in {SpecialistRunStatus.BLOCKED, SpecialistRunStatus.FAILED}
                else None
            ),
            clarification_question=(
                "Which button should change?"
                if status is SpecialistRunStatus.CLARIFICATION_REQUIRED
                else None
            ),
        )
    )
    return SpecialistRunResult(
        specialist=specialist,
        mode=SEOExecutionMode.EDIT,
        assignment=assignment or f"Perform {specialist.value} edit.",
        status=status,
        completion=completion,
        patch_results=patches,
        changed_files=sorted(
            {patch.file for patch in patches if patch.status is PatchStatus.APPLIED}
        ),
        applied_patch_count=sum(patch.status is PatchStatus.APPLIED for patch in patches),
        rejected_patch_count=sum(patch.status is PatchStatus.REJECTED for patch in patches),
        latency_ms=10.0,
        token_usage=TokenUsage(),
        model="groq/test-model",
        error="Internal specialist failure." if status is SpecialistRunStatus.FAILED else None,
        style_changes=(
            [
                StyleChangeEvidence(
                    target_id="index.hero.project_cta",
                    label="Start a project button",
                    selector=".button-link",
                    property_name="background",
                    before_value="green",
                    after_value="#16a34a",
                    expected_relation="equals_requested",
                )
            ]
            if specialist is SpecialistName.CSS
            and status is SpecialistRunStatus.ALREADY_SATISFIED
            else []
        ),
    )


def empty_diff(run_id: str = "test-run") -> DiffReport:
    return DiffReport(run_id=run_id)


def request() -> EditRequest:
    return EditRequest(target_page="index.html", instruction="Apply the requested edit.")
