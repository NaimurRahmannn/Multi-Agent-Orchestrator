from pathlib import Path

from crewai.flow import Flow

from agentorchestra.flow import AgentOrchestraFlow
from agentorchestra.models import (
    CriterionResult,
    EditRequest,
    ManagerRoutingPlan,
    ManagerRunResult,
    QAResult,
    SpecialistName,
    TokenUsage,
)
from agentorchestra.pipeline_models import QARunResult
from agentorchestra.services.workspace import propose_patch
from agentorchestra.specialist_models import SpecialistExecutionReport
from tests.specialist_helpers import execute_plan, run_result
from tests.test_workspace_service import make_settings


class FakeRouter:
    def __init__(self, plan, *, fail=False):
        self.plan = plan
        self.fail = fail
        self.calls = []

    def route(self, request):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("manager-secret failed")
        return ManagerRunResult(
            request=request,
            plan=self.plan,
            latency_ms=5.0,
            token_usage=TokenUsage(),
            model="groq/manager",
        )


class FakeSpecialists:
    def __init__(self, statuses):
        self.statuses = statuses
        self.calls = []

    def execute(self, request, plan, workspace):
        self.calls.append([specialist.value for specialist in plan.selected_specialists])
        results = []
        for assignment, status in zip(plan.assignments, self.statuses, strict=False):
            patches = []
            if status == "succeeded":
                if assignment.agent is SpecialistName.CSS:
                    patches.append(
                        propose_patch(
                            workspace,
                            specialist=SpecialistName.CSS,
                            file="style.css",
                            old_text="  background: var(--accent);\n",
                            new_text="  background: #0b3d91;\n",
                            summary="Apply CSS edit.",
                        )
                    )
                else:
                    patches.append(
                        propose_patch(
                            workspace,
                            specialist=SpecialistName.HTML,
                            file=request.target_page,
                            old_text="  <h1>Home</h1>\n",
                            new_text='  <h1 id="home-hero-title">Home</h1>\n',
                            summary="Apply HTML edit.",
                        )
                    )
            results.append(
                run_result(
                    assignment.agent,
                    status,
                    assignment=assignment.task,
                    patches=patches,
                )
            )
            if status in {"blocked", "failed"}:
                break
        from agentorchestra.services.workspace import generate_diff

        diff = generate_diff(workspace, settings=self.settings)
        return SpecialistExecutionReport(
            run_id=workspace.run_id,
            request=request,
            plan=plan,
            status="succeeded" if all(status == "succeeded" for status in self.statuses) else self.statuses[-1],
            results=results,
            diff_report=diff,
            total_latency_ms=sum(result.latency_ms for result in results),
            stopped_early=len(results) < len(plan.selected_specialists),
        )


class FakeQA:
    def __init__(self, verdict="accept", *, mutate_root: Path | None = None):
        self.verdict = verdict
        self.calls = []
        self.mutate_root = mutate_root

    def run(self, evidence):
        self.calls.append(evidence)
        if self.mutate_root is not None:
            (self.mutate_root / evidence.run_id / "style.css").write_text(
                "body { color: red; }\n",
                encoding="utf-8",
            )
        status = "passed" if self.verdict == "accept" else "failed"
        return QARunResult(
            result=QAResult(
                verdict=self.verdict,
                criteria_results=[
                    CriterionResult(
                        criterion=evidence.acceptance_criteria[0],
                        status=status,
                        evidence="Evidence from diff.",
                    )
                ],
                reason="QA decision.",
            ),
            latency_ms=2.0,
            token_usage=TokenUsage(),
            model="groq/qa",
            evidence_digest=evidence.evidence_digest,
        )


def build_flow(tmp_path, plan, statuses=("succeeded",), qa=None, router=None):
    settings = make_settings(tmp_path)
    specialists = FakeSpecialists(statuses)
    specialists.settings = settings
    return (
        settings,
        specialists,
        qa or FakeQA(),
        AgentOrchestraFlow(
            settings=settings,
            router=router or FakeRouter(plan),
            specialist_service=specialists,
            qa_runner=qa or FakeQA(),
        ),
    )


def staged_run_dirs(settings):
    if not settings.staging_root_dir.exists():
        return []
    return sorted(path.name for path in settings.staging_root_dir.iterdir() if path.is_dir())


def test_agentorchestra_flow_is_crewai_flow():
    assert issubclass(AgentOrchestraFlow, Flow)


def test_flow_accepts_and_promotes_css_edit(tmp_path):
    settings = make_settings(tmp_path)
    plan = execute_plan("css")
    specialists = FakeSpecialists(("succeeded",))
    specialists.settings = settings
    qa = FakeQA("accept")
    flow = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(plan),
        specialist_service=specialists,
        qa_runner=qa,
    )

    report = flow.run(EditRequest(target_page="index.html", instruction="Change CSS."))

    assert report.status == "accepted"
    assert report.working_updated is True
    assert report.staging_cleaned is True
    assert qa.calls
    assert "background: #0b3d91" in (settings.working_site_dir / "style.css").read_text()
    assert staged_run_dirs(settings) == []


def test_flow_rejects_without_promotion(tmp_path):
    settings = make_settings(tmp_path)
    plan = execute_plan("css")
    specialists = FakeSpecialists(("succeeded",))
    specialists.settings = settings
    qa = FakeQA("reject")
    flow = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(plan),
        specialist_service=specialists,
        qa_runner=qa,
    )

    report = flow.run(EditRequest(target_page="index.html", instruction="Change CSS."))

    assert report.status == "rejected"
    assert report.working_updated is False
    assert "var(--accent)" in (settings.working_site_dir / "style.css").read_text()
    assert staged_run_dirs(settings) == []


def test_flow_clarification_out_of_scope_and_seo_create_no_staging(tmp_path):
    for index, plan in enumerate(
        [
            ManagerRoutingPlan(
                status="clarification_required",
                request_type="clarify",
                selected_specialists=[],
                routing_rationale="Need more detail.",
                assignments=[],
                acceptance_criteria=[],
                clarification_question="What should change?",
                rejection_reason=None,
            ),
            ManagerRoutingPlan(
                status="out_of_scope",
                request_type="unsupported",
                selected_specialists=[],
                routing_rationale="Not supported.",
                assignments=[],
                acceptance_criteria=[],
                clarification_question=None,
                rejection_reason="Unsupported.",
            ),
            execute_plan("seo"),
        ]
    ):
        settings = make_settings(tmp_path / str(index))
        qa = FakeQA()
        specialists = FakeSpecialists(())
        specialists.settings = settings
        report = AgentOrchestraFlow(
            settings=settings,
            router=FakeRouter(plan),
            specialist_service=specialists,
            qa_runner=qa,
        ).run(EditRequest(target_page="index.html", instruction="Request."))

        assert report.working_updated is False
        assert not qa.calls
        assert staged_run_dirs(settings) == []


def test_flow_blocks_before_qa_on_specialist_block(tmp_path):
    settings = make_settings(tmp_path)
    plan = execute_plan("css")
    specialists = FakeSpecialists(("blocked",))
    specialists.settings = settings
    qa = FakeQA()

    report = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(plan),
        specialist_service=specialists,
        qa_runner=qa,
    ).run(EditRequest(target_page="index.html", instruction="Change CSS."))

    assert report.status == "blocked"
    assert not qa.calls
    assert report.staging_cleaned is True


def test_flow_fails_when_staging_changes_after_qa(tmp_path):
    settings = make_settings(tmp_path)
    plan = execute_plan("css")
    specialists = FakeSpecialists(("succeeded",))
    specialists.settings = settings
    qa = FakeQA("accept", mutate_root=settings.staging_root_dir)

    report = AgentOrchestraFlow(
        settings=settings,
        router=FakeRouter(plan),
        specialist_service=specialists,
        qa_runner=qa,
    ).run(EditRequest(target_page="index.html", instruction="Change CSS."))

    assert report.status == "failed"
    assert report.working_updated is False
    assert "Reviewed staged diff changed" in report.error
    assert "var(--accent)" in (settings.working_site_dir / "style.css").read_text()
