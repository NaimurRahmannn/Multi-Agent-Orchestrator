import pytest
from pydantic import ValidationError

from agentorchestra.models import ManagerRoutingPlan, SpecialistAssignment
from agentorchestra.seo_models import (
    LighthouseAuditItem,
    LighthouseSEOResult,
    SEOCompletion,
    SEODiagnosticReport,
    SEOFinding,
)


def finding(code="missing_description"):
    return SEOFinding(
        code=code,
        severity="warning",
        title="Meta description is missing",
        source_file="index.html",
        evidence="The selected source has no meta description element.",
        recommendation="Add one concise description in the head.",
    )


def lighthouse(run_id="seo-run"):
    return LighthouseSEOResult(
        status="succeeded",
        run_id=run_id,
        target_page="index.html",
        score=92,
        audits=[
            LighthouseAuditItem(
                audit_id="document-title",
                title="Document has a title",
                status="passed",
                score=100,
            )
        ],
        failed_audit_ids=[],
        report_path=f"reports/lighthouse/seo-{run_id}.json",
        latency_ms=10.0,
    )


def test_seo_completion_modes_are_strict():
    edit = SEOCompletion(mode="edit", status="completed", summary="Changed the title.")
    diagnostic = SEOCompletion(
        mode="diagnostic",
        status="completed",
        summary="Reviewed source.",
        findings=[finding()],
    )

    assert edit.findings == []
    assert diagnostic.findings[0].code == "missing_description"
    with pytest.raises(ValidationError):
        SEOCompletion(mode="diagnostic", status="completed", summary="No findings.")
    with pytest.raises(ValidationError):
        SEOCompletion(mode="edit", status="completed", summary="Bad.", findings=[finding()])
    with pytest.raises(ValidationError):
        SEOCompletion(
            mode="diagnostic",
            status="completed",
            summary="Duplicates.",
            findings=[finding(), finding()],
        )


def test_lighthouse_and_diagnostic_contracts_reject_mismatches():
    audit = lighthouse()
    report = SEODiagnosticReport(
        run_id="seo-run",
        target_page="index.html",
        findings=[finding()],
        lighthouse=audit,
        source_unchanged=True,
    )
    assert report.lighthouse == audit

    with pytest.raises(ValidationError):
        SEODiagnosticReport(
            run_id="other",
            target_page="index.html",
            findings=[finding()],
            lighthouse=audit,
        )
    with pytest.raises(ValidationError):
        LighthouseSEOResult(
            status="succeeded",
            run_id="seo-run",
            target_page="index.html",
            score=90,
            failed_audit_ids=["missing"],
            report_path="reports/lighthouse/seo.json",
            latency_ms=1.0,
        )


def test_manager_seo_request_type_and_diagnostic_selection_are_strict():
    assignment = SpecialistAssignment(agent="seo", task="Review source SEO.")
    with pytest.raises(ValidationError):
        ManagerRoutingPlan(
            status="execute",
            request_type="route",
            selected_specialists=["seo"],
            routing_rationale="SEO owns metadata.",
            assignments=[assignment],
            acceptance_criteria=["SEO is reviewed."],
        )
    with pytest.raises(ValidationError):
        ManagerRoutingPlan(
            status="execute",
            request_type="seo_diagnostic",
            selected_specialists=["seo", "css"],
            routing_rationale="Invalid mixed diagnosis.",
            assignments=[
                assignment,
                SpecialistAssignment(agent="css", task="Change CSS."),
            ],
            acceptance_criteria=["SEO is reviewed."],
        )
