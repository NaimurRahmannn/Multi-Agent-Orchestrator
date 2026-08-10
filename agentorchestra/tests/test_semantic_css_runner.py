from agentorchestra.models import (
    EditRequest,
    ManagerRoutingPlan,
    SpecialistAssignment,
    SpecialistName,
)
from agentorchestra.services.specialist_execution import SpecialistExecutionService
from agentorchestra.services.specialist_runner import SpecialistRunner
from agentorchestra.services.workspace import create_staged_copy
from agentorchestra.specialist_models import SpecialistExecutionStatus, SpecialistRunStatus
from tests.specialist_helpers import execute_plan
from tests.test_workspace_service import make_settings


def test_common_css_request_executes_without_css_model_or_raw_patch_generation(tmp_path):
    settings = make_settings(tmp_path)
    workspace = create_staged_copy(settings=settings, run_id_factory=lambda: "semantic-runner")
    runner = SpecialistRunner(settings=settings, clock=iter([1.0, 1.01]).__next__)

    result = runner.run_specialist(
        EditRequest(
            target_page="index.html",
            instruction="Change the Start a project button to red.",
        ),
        SpecialistAssignment(
            agent=SpecialistName.CSS,
            task="Make the Start a project button red.",
        ),
        ["The Start a project button has a red background."],
        workspace,
    )

    assert result.status is SpecialistRunStatus.SUCCEEDED
    assert result.model == "deterministic/css-semantic-v1"
    assert result.token_usage.total_tokens is None
    assert result.applied_patch_count == 1
    assert result.changed_files == ["style.css"]
    assert result.style_plan is not None
    assert result.style_changes[0].source_verified is True
    assert "background: #dc2626;" in (workspace.path / "style.css").read_text()


def test_already_satisfied_css_request_is_not_reported_as_blocked(tmp_path):
    settings = make_settings(tmp_path)
    stylesheet = settings.working_site_dir / "style.css"
    stylesheet.write_text(
        stylesheet.read_text().replace("background: var(--accent);", "background: #dc2626;"),
        encoding="utf-8",
    )
    workspace = create_staged_copy(settings=settings, run_id_factory=lambda: "semantic-noop")
    request = EditRequest(
        target_page="index.html",
        instruction="Change the Start a project button to red.",
    )
    plan_payload = execute_plan("css").model_dump(mode="python")
    plan_payload["assignments"] = [
        SpecialistAssignment(
            agent=SpecialistName.CSS,
            task="Change the Start a project button to red.",
        )
    ]
    plan = ManagerRoutingPlan.model_validate(plan_payload)
    report = SpecialistExecutionService(settings=settings).execute(
        request,
        plan,
        workspace,
    )

    assert report.status is SpecialistExecutionStatus.ALREADY_SATISFIED
    assert report.diff_report.is_empty
    assert report.results[0].status is SpecialistRunStatus.ALREADY_SATISFIED
