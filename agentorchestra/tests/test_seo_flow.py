from agentorchestra.flow import AgentOrchestraFlow
from agentorchestra.models import (
    EditRequest,
    ManagerRoutingPlan,
    SpecialistAssignment,
    SpecialistName,
    TokenUsage,
)
from agentorchestra.seo_models import (
    LighthouseAuditItem,
    LighthouseSEOResult,
    SEOCompletion,
    SEOExecutionMode,
    SEOFinding,
)
from agentorchestra.services.workspace import generate_diff, propose_patch
from agentorchestra.specialist_models import SpecialistExecutionReport, SpecialistRunResult
from tests.specialist_helpers import execute_plan, run_result
from tests.test_edit_flow import FakeQA, FakeRouter, staged_run_dirs
from tests.test_workspace_service import make_settings


def diagnostic_plan():
    return ManagerRoutingPlan(
        status="execute",
        request_type="seo_diagnostic",
        selected_specialists=["seo"],
        routing_rationale="SEO owns source diagnosis.",
        assignments=[SpecialistAssignment(agent="seo", task="Diagnose source SEO.")],
        acceptance_criteria=["Return source-grounded SEO findings."],
    )


def successful_lighthouse(workspace, target_page, **kwargs):
    return LighthouseSEOResult(
        status="succeeded",
        run_id=workspace.run_id,
        target_page=target_page,
        score=90,
        audits=[
            LighthouseAuditItem(
                audit_id="document-title",
                title="Document has a title",
                status="passed",
                score=100,
            )
        ],
        failed_audit_ids=[],
        report_path=f"reports/lighthouse/seo-{workspace.run_id}.json",
        latency_ms=5.0,
    )


class SEOService:
    def __init__(self, settings, *, diagnostic=False):
        self.settings = settings
        self.diagnostic = diagnostic
        self.calls = []

    def execute(self, request, plan, workspace):
        self.calls.append(plan)
        assignment = plan.assignments[0]
        if self.diagnostic:
            finding = SEOFinding(
                code="missing_description",
                severity="warning",
                title="Meta description is missing",
                source_file=request.target_page,
                evidence="No meta description appears in the selected source.",
                recommendation="Add one concise description in the head.",
            )
            completion = SEOCompletion(
                mode="diagnostic",
                status="completed",
                summary="Reviewed source SEO.",
                findings=[finding],
            )
            result = SpecialistRunResult(
                specialist="seo",
                mode="diagnostic",
                assignment=assignment.task,
                status="succeeded",
                completion=completion,
                patch_results=[],
                changed_files=[],
                applied_patch_count=0,
                rejected_patch_count=0,
                latency_ms=1.0,
                token_usage=TokenUsage(),
                model="groq/seo",
            )
        else:
            patch = propose_patch(
                workspace,
                specialist=SpecialistName.SEO,
                file=request.target_page,
                old_text="  <title>Home</title>\n",
                new_text="  <title>Harbor Light Web Design Studio</title>\n",
                summary="Improve the page title.",
                allowed_files=(request.target_page,),
            )
            result = run_result(
                "seo",
                assignment=assignment.task,
                patches=[patch],
            )
        diff = generate_diff(workspace, settings=self.settings)
        return SpecialistExecutionReport(
            run_id=workspace.run_id,
            request=request,
            plan=plan,
            status="succeeded",
            results=[result],
            diff_report=diff,
            total_latency_ms=result.latency_ms,
            stopped_early=False,
            seo_mode=(SEOExecutionMode.DIAGNOSTIC if self.diagnostic else SEOExecutionMode.EDIT),
        )


def run_flow(tmp_path, *, diagnostic=False, verdict="accept", lighthouse=successful_lighthouse):
    settings = make_settings(tmp_path)
    plan = diagnostic_plan() if diagnostic else execute_plan("seo")
    qa = FakeQA(verdict)
    flow = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(plan),
        specialist_service=SEOService(settings, diagnostic=diagnostic),
        qa_runner=qa,
        lighthouse_runner=lighthouse,
    )
    report = flow.kickoff(
        inputs={
            "request": EditRequest(
                target_page="index.html",
                instruction="Diagnose SEO." if diagnostic else "Improve the page title.",
            ).model_dump(mode="json")
        }
    )
    return settings, qa, report


def test_seo_edit_accepts_with_matching_lighthouse_then_promotes(tmp_path):
    settings, qa, report = run_flow(tmp_path, verdict="accept")

    assert report.status == "accepted", report.error
    assert report.lighthouse_seo.score == 90
    assert qa.calls[0].lighthouse_seo == report.lighthouse_seo
    assert "Harbor Light Web Design" in (settings.working_site_dir / "index.html").read_text()
    assert staged_run_dirs(settings) == []


def test_seo_edit_qa_rejection_discards_staging(tmp_path):
    settings, qa, report = run_flow(tmp_path, verdict="reject")

    assert report.status == "rejected"
    assert qa.calls
    assert "<title>Home</title>" in (settings.working_site_dir / "index.html").read_text()
    assert "<title>Home</title>" in (settings.fixture_site_dir / "index.html").read_text()
    assert staged_run_dirs(settings) == []


def test_failed_lighthouse_prevents_qa_and_discards_staging(tmp_path):
    def failed(workspace, target_page, **kwargs):
        return LighthouseSEOResult(
            status="failed",
            run_id=workspace.run_id,
            target_page=target_page,
            latency_ms=2.0,
            error="Lighthouse SEO audit failed.",
        )

    settings, qa, report = run_flow(tmp_path, lighthouse=failed)

    assert report.status == "failed"
    assert qa.calls == []
    assert report.working_updated is False
    assert staged_run_dirs(settings) == []


def test_seo_diagnostic_returns_findings_and_audit_without_qa_or_promotion(tmp_path):
    settings, qa, report = run_flow(tmp_path, diagnostic=True)

    assert report.status == "diagnostic_completed"
    assert report.seo_diagnostic_report.findings[0].code == "missing_description"
    assert report.lighthouse_seo.score == 90
    assert qa.calls == []
    assert report.qa_run is None
    assert report.promotion_result is None
    assert report.working_updated is False
    assert report.reviewed_diff.is_empty
    assert report.staging_cleaned is True
    assert "<title>Home</title>" in (settings.working_site_dir / "index.html").read_text()
    assert staged_run_dirs(settings) == []


def test_non_seo_flow_never_calls_lighthouse(tmp_path):
    from tests.test_edit_flow import FakeSpecialists

    settings = make_settings(tmp_path)
    specialists = FakeSpecialists(("succeeded",))
    specialists.settings = settings
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Lighthouse must not run")

    report = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(execute_plan("css")),
        specialist_service=specialists,
        qa_runner=FakeQA("reject"),
        lighthouse_runner=forbidden,
    ).kickoff(inputs={"request": {"target_page": "index.html", "instruction": "Change CSS."}})

    assert report.status == "rejected"
    assert report.lighthouse_seo is None
    assert calls == []
